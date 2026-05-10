# Pi Productivity Assistant

A Raspberry Pi-based productivity assistant that communicates via Telegram. Multi-tenant: Xander (`America/Chicago`) and Mom (`America/Los_Angeles`) share one Pi, one bot token, one SQLite DB. No data leakage between users.

## Deployment context

- **Development**: local laptop (this repo)
- **Production**: Raspberry Pi at `/home/xanderabbott/assistant`
- **DB on Pi**: `/home/xanderabbott/assistant/data/data.db`
- **Handoff**: laptop → GitHub (`github.com/xander-abbott/pi-assistant`) → Pi via `git pull`
- **Pi user**: `xanderabbott` (NOT `pi`)

## Architecture: Python tick loop

One cron entry runs `venv/bin/python sender.py tick` every 15 minutes. Tick iterates active users, resolves each user's local time, fires scheduled events that haven't yet fired today. Schedule is data in Python (`schedule.py`), not cron entries.

Three cron entries total (see `scheduler/crontab.example`):

```
*/15 * * * *  sender.py tick          # scheduler — fires all due messages
30 23 * * *   etl/link_responses.py   # link checkin responses to goals
0  6  * * 1   etl/load_weekly_goals.py  # load Monday's goals before first tick
```

## Telegram

One bot, one token, multiple chats. Telegram isolates by `chat_id`; the application enforces user separation in the DB.

**Onboarding a new user**: user opens the bot → sends any message → bot replies with their `chat_id` (the `/start` handler does this even for unregistered users) → admin inserts a row in `users`:

```sql
INSERT INTO users (name, chat_id, timezone, schedule_profile)
VALUES ('name', <chat_id>, 'America/Timezone', 'default');
```

`bot.py` rejects unknown `chat_id`s with a polite reply and **no DB write** before the user row exists.

## Database schema

Seven tables. All timestamps stored as UTC (`datetime('now')` in SQLite is UTC).

```sql
CREATE TABLE users (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    chat_id          INTEGER NOT NULL UNIQUE,
    timezone         TEXT    NOT NULL,
    schedule_profile TEXT    NOT NULL DEFAULT 'default',
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE days (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    date       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, date)
);

-- sent_messages, responses: reference day_id; user-scoped via the FK chain
-- goals, goal_completions, weekly_grades: user_id column for direct scoping
```

`timezone` stores IANA names only (`America/Chicago`, `America/Los_Angeles`). Never abbreviations.

`sent_messages` and `responses` have no `user_id` column — they inherit user scoping through `day_id → days.user_id`. Do not denormalize. When you need user-scoped queries on `goal_completions`, JOIN through `days`.

## Schedule definition (`schedule.py`)

```python
SCHEDULES = {
    "default": {
        "morning_greeting": {"weekday": time(7, 0), "weekend": time(10, 0)},
        "breakfast_checkin": time(9, 0),
        "lunch_checkin":     time(12, 0),
        "dinner_checkin":    time(19, 0),
        "eod_checkin":       time(21, 0),
    },
}

WEEKLY_EVENTS = {
    "default": {
        "weekly_grade_wed": {"day_of_week": 2, "time": time(12, 0)},  # Wednesday noon
        "weekly_grade_fri": {"day_of_week": 4, "time": time(19, 0)},  # Friday 7 PM
        "weekly_grade_sun": {"day_of_week": 6, "time": time(12, 0)},  # Sunday noon
    },
}
```

The `WEEKLY_EVENTS` keys are the same strings as the `messages.WEEKLY_GRADE_*` constants. This is intentional: `send_grade` records `db.record_sent_message(day_id, msg_key)` using those same strings, so `should_fire`'s `was_message_sent` check prevents the grade from re-firing every 15 minutes.

`should_fire(user, message_key, scheduled_local_time, now_utc, db_module)` takes `db_module` as an injectable parameter for testability. The tick loop passes the real `db` module; tests pass `SimpleNamespace` mocks.

## Timezone handling

**Core principle**: store everything in UTC, convert at the edges.

`get_or_create_day(user_id)` computes "today" in the user's local timezone, not the Pi's system time. Without this, a late-evening Pacific user's log lands on tomorrow's Pi-local Central day.

The Sunday noon cutoff for grading is anchored to the user's local noon, converted to UTC for the SQLite string comparison. SQLite's `datetime('now')` produces space-separated strings (`"YYYY-MM-DD HH:MM:SS"`); the cutoff string must use the same format — not ISO 8601 T-format.

Pacific and Central observe DST on the same days; the 2-hour gap is stable year-round. The tick's `*/15` cadence is idempotent (won't double-send because of the `was_message_sent` guard), so DST fall-back duplicate tick firings are harmless.

## Tick loop (`sender.py tick`)

The tick captures a single `now_utc` timestamp for the entire run, then iterates active users. Per-user exceptions are caught and logged — one user's failure never kills others.

For each user and each scheduled event:

1. `should_fire` checks timing window and `was_message_sent`.
2. If a grade key (`weekly_grade_wed/fri/sun`): call `compute_grade.compute_grade_args_for_user` then `asyncio.run(compute_grade.send_grade(...))`. `send_grade` handles send + all DB records internally.
3. Otherwise: `build_text(key, user)` → `asyncio.run(_telegram_send(...))` → `db.record_sent_message(day_id, key)`.

**Send-before-record ordering**: Telegram send happens first. Only if the send succeeds is the DB record written. Send failure → no record → tick retries next window (acceptable duplicate). Send success + record failure → logged as ALERT (recoverable duplicate on next tick).

Every tick run logs a summary line:

```
[INFO] tick: checked 2 users, fired 1, skipped 9 (already-sent: 3, not-due: 6)
```

Grep for `fired 0` on days you expected sends.

## Dynamic message builders

All builders take a `user` dict and derive local date from `user["timezone"]`:

- `_build_morning_report(user)`: week score + per-goal status using `db.get_all_goals_with_status`.
- `_build_eod_checkin(user)`: lists essential keys (breakfast/lunch/dinner/sleep) not yet logged today. Returns `None` if all logged (tick skips the send).
- `_build_backlog_nudge(user)`: lists goals behind pace with tone scaled to weekday.

## Grade events (`etl/compute_grade.py`)

`compute_grade_args_for_user(user, message_key) → (week_key, cutoff)`:
- For `weekly_grade_sun`: cutoff = user's local noon in UTC, SQLite format.
- For `weekly_grade_wed/fri`: cutoff = `None` (all completions in the week count).

`send_grade(user, week_key, msg_key, cutoff)` is async and called via `asyncio.run()` from the tick loop. It: computes grade → records `weekly_grades` row → formats message → sends via Telegram → marks grade as sent → records `sent_messages`.

`etl/compute_grade.py` also has a standalone `main()` that infers `msg_key` from the current local weekday — usable from cron independently if ever needed again.

## Per-user data files

Weekly goals live at `data/weekly_goals/<user_id>.json`. `etl/load_weekly_goals.py` iterates all active users and loads each user's file. If a user's file is missing, the script logs a warning and continues for other users.

`etl/link_responses.py` runs nightly and links today's checkin responses to weekly goals, recording `goal_completions` rows. Per-user, per-week, with the Sunday noon cutoff respected.

## Goal completion guards (`db.record_goal_completion`)

Two defensive checks before inserting:
1. `day_id` must belong to `user_id` (guards against cross-user day_id confusion).
2. `goal_id` must exist for `user_id` in `week_key` (guards against stale or misspelled goal IDs).

Both raise `ValueError` on failure.

## Hard rules

1. Unknown `chat_id`s: `bot.py` rejects with a polite reply and **no DB write**.
2. IANA timezone names only — `ZoneInfo()` will raise on invalid names, which is the desired failure mode.
3. `received_at` / `sent_at` / `completed_at` stay UTC. Never store local time in the DB.
4. `get_or_create_day` uses user's local date, not `date.today()`.
5. Sunday noon cutoff must be anchored to user's local time and converted to UTC SQLite format for string comparison.
6. Tick loop logs a summary line every run.
7. `message_key` string values in `messages.py` are stable — renaming orphans all historical `sent_messages` and `responses` rows.
8. Grade event schedule keys must equal the `messages.WEEKLY_GRADE_*` constants so `was_message_sent` prevents re-fires.

## Gotchas

- `python-telegram-bot` uses long polling — CPU impact near zero. `poll_interval=5.0` configured.
- Kitty terminal needs `export TERM=xterm-256color` when using `nano` over SSH.
- Pi project path is `/home/xanderabbott/assistant`. Do not use `/home/pi/assistant`.
- SQLite `ALTER TABLE ADD COLUMN` cannot add a NOT NULL FK column. Any future table schema changes require the create-new-copy-drop-rename pattern with `PRAGMA foreign_keys=OFF` outside the transaction and `PRAGMA foreign_key_check` inside before commit.
- `config.py` no longer has a `CHAT_ID` global. Per-user `chat_id` lives in the `users` table. `.env` only needs `BOT_TOKEN`.

## Deployment history

- **Phase 1** (single-user): basic checkins, meals, morning greeting for Xander only. Cron-per-message pattern.
- **Multi-tenant refactor** (2026-05-09): tick loop pattern, multi-user DB schema with `users` table, per-user timezone handling, weekly goals scoped per user, grade events wired into tick loop. Migration script: `migrate_multitenant.py`.
- **Mom onboarded**: YYYY-MM-DD (pending)
