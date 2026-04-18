# Pi Productivity Assistant

A Raspberry Pi-based personal productivity assistant communicating via Telegram. Tracks daily meals, workouts, chores, sleep, and weekly goals. Rules-based only — no LLM calls at runtime.

## Deployment

- **Hardware:** Raspberry Pi 5 (4GB), running headlessly
- **Username:** `xanderabbott`
- **Project root:** `/home/xanderabbott/assistant/`
- **Venv:** `/home/xanderabbott/assistant/venv/`
- **Database:** `/home/xanderabbott/assistant/data/data.db` (note the `data/` subdirectory)
- **Timezone:** America/Chicago (CDT)
- **Scheduling:** cron (one-shot senders) + systemd (always-on polling bot)

Never reference `/home/pi/` — that username does not exist on this machine.

---

## Project Status

**Phase 1 — COMPLETE and deployed.** Bot is live, crontab installed, systemd service running. Do not break existing behavior.

**Phase 2 — TO BE BUILT.** Spec is fully designed. See Phase 2 section below for implementation details.

---

## Architecture

- **`sender.py`** — one-shot script called by cron to send a scheduled message by key
- **`bot.py`** — long-running polling process (systemd service) that receives and records user replies
- **`db.py`** — all SQLite interactions; schema auto-creates on `init_db()`
- **`messages.py`** — all message text and `message_key` constants; edit here to change bot copy
- **`config.py`** — loads `.env`, fails fast if `BOT_TOKEN` or `CHAT_ID` missing
- **`etl/`** — data processing scripts (Phase 2); separate from collection logic
- **`data/weekly_goals.json`** — user-authored weekly goals file (Phase 2); never committed to git
- **`data/time_blocks.json`** — scheduled block config (Phase 2); read at runtime

---

## Key Design Invariants

These must be respected across all changes:

1. **`message_key` values are permanent.** They are stored in the database. Renaming a key orphans historical data. Only add new keys; never rename existing ones.
2. **`raw_text` is never modified.** Responses are stored verbatim. `parsed_value` is written separately so the parser can be fixed retroactively.
3. **No LLM calls at runtime.** All message generation uses pre-written templates with variable substitution. Everything must work offline on the Pi.
4. **Exact prefix matching only.** `KEY_PREFIXES` uses `startswith` — no fuzzy matching, no NLP. A non-matching message produces an explicit error reply.
5. **ETL is separate from collection.** Scripts in `etl/` process data after the fact; they never modify `raw_text` on existing rows.
6. **`DB_PATH` always uses the full absolute path.** Tilde expansion behaves unexpectedly with `scp` and some subprocess calls. Always use `/home/xanderabbott/assistant/data/data.db`.

---

## Existing Database Schema (Phase 1)

```sql
days           — one row per calendar date (id, date, created_at)
sent_messages  — every outbound message (id, day_id, message_key, sent_at, attempt)
responses      — every user reply (id, day_id, message_key, raw_text, parsed_value, received_at)
```

`get_unanswered_keys(day_id)` LEFT JOINs `sent_messages` vs `responses` to find gaps — drives re-ask logic. `parsed_value` is currently unused and reserved for Phase 2 goal linking.

---

## Existing Message Keys (Phase 1)

Defined as constants in `messages.py`. Do not rename.

```
morning_greeting
breakfast_checkin
lunch_checkin
dinner_checkin
eod_checkin
```

---

## Existing Prefix Rules (Phase 1)

In `bot.py`, `infer_message_key()` uses exact `startswith` matching (lowercased):

| Prefix(es) | Maps to |
|---|---|
| `breakfast:` | `breakfast_checkin` |
| `lunch:` | `lunch_checkin` |
| `dinner:` | `dinner_checkin` |
| `workout:`, `chore:`, `gym:`, `run:`, `calisthenics:`, `eod:` | `eod_checkin` |

If no prefix matches, falls back to `get_last_sent_key(day_id)`.

---

## Phase 2 Specification

### Overview

Phase 2 adds weekly goal tracking, time-blocked daily structure, backlog pressure, and automated intra-week grading. It extends Phase 1 without replacing it.

**Weekly rhythm:**
- **Mon–Fri:** Execution. Structured check-ins, log completions, surface backlog.
- **Saturday:** Recovery buffer. Last chance to hit weekly targets. Backlog surfaced prominently.
- **Sunday:** Reflection and reset. Grade published at noon. Planning prompt at 8 PM.

**Sunday noon cutoff:** Completions logged before 12:00 PM Sunday count toward that week's grade. Grade is computed and sent at noon. Nothing after noon rolls back into the closed week.

---

### New Files to Create

```
etl/load_weekly_goals.py   — ingest weekly_goals.json into goals table
etl/compute_grade.py       — compute score, write weekly_grades, send Telegram message
etl/link_responses.py      — match responses to goal_ids, write parsed_value nightly
data/weekly_goals.json     — user-authored (gitignored); keyed by ISO week string
data/time_blocks.json      — block schedule config; send/re-ask times per category
```

---

### New Database Tables (Phase 2)

Add these to the `DDL` string in `db.py`:

```sql
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY,
    week_key    TEXT NOT NULL,           -- e.g. '2026-W17'
    goal_id     TEXT NOT NULL,           -- e.g. 'w1'; unique per week_key
    category    TEXT NOT NULL,           -- workout / nutrition / chore / personal
    label       TEXT NOT NULL,           -- human-readable; used for Done: matching
    target_days INTEGER NOT NULL,        -- completions needed this week (weekly total, not per-day)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(week_key, goal_id)
);

CREATE TABLE IF NOT EXISTS goal_completions (
    id           INTEGER PRIMARY KEY,
    day_id       INTEGER NOT NULL REFERENCES days(id),
    week_key     TEXT NOT NULL,
    goal_id      TEXT NOT NULL,
    response_id  INTEGER REFERENCES responses(id),  -- NULL if logged via Done: prefix
    source       TEXT NOT NULL,                      -- 'scheduled' or 'proactive'
    completed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weekly_grades (
    id              INTEGER PRIMARY KEY,
    week_key        TEXT NOT NULL,
    graded_at       TEXT NOT NULL DEFAULT (datetime('now')),
    score_pct       REAL NOT NULL,          -- completions / target_days, 0.0–1.0
    breakdown_json  TEXT NOT NULL,          -- per-category scores as JSON
    message_sent    INTEGER NOT NULL DEFAULT 0
);
```

---

### New Message Keys (Phase 2)

Add these constants to `messages.py` and add corresponding entries to `MESSAGE_TEXT` and `REASK_PREFIX` where applicable:

```
sleep_log
weekly_grade_wed
weekly_grade_fri
weekly_grade_sun
weekly_planning_prompt
backlog_nudge
```

---

### New Prefix Rules (Phase 2)

Extend `KEY_PREFIXES` in `messages.py` and update `infer_message_key()` in `bot.py`:

| Prefix | Maps to | Notes |
|---|---|---|
| `sleep:` | `sleep_log` | e.g. `Sleep: 7.5` |
| `done:` | `goal_completion` | Proactive completion — special handling required (see below) |

**`Done:` prefix handling** is distinct from all other prefixes. When `bot.py` receives a `done:` message, it must:
1. Extract the text after `done:`, strip and lowercase it
2. Query `goals` for the current `week_key`, compare against `label` (case-insensitive exact match)
3. If matched: insert a row into `goal_completions` with `source = 'proactive'`, `response_id = NULL`; reply "Got it — [label] marked complete."
4. If no match: reply "Couldn't match that to a goal this week. Check your goal labels and try again, or log it manually."

Do not route `done:` through `record_response()` — it writes directly to `goal_completions`.

---

### Time-Block Schedule

Defined in `data/time_blocks.json`. Default values below. All times America/Chicago.

**Weekdays (Mon–Fri):**

| Block key | Send at | Re-ask at | Category |
|---|---|---|---|
| `morning_greeting` | 7:00 AM | — | Daily preview |
| `breakfast_checkin` | 8:30 AM | 11:00 AM | Nutrition + sleep log |
| `lunch_checkin` | 12:00 PM | 2:00 PM | Nutrition |
| `eod_checkin` (workout) | 6:15 PM | 9:00 PM | Workout / activity |
| `dinner_checkin` | 7:00 PM | 9:30 PM | Nutrition |
| `eod_checkin` (wrap) | 9:30 PM | 11:00 PM | All-category catch-all |

**Saturday:**

| Block key | Send at | Re-ask at | Notes |
|---|---|---|---|
| `morning_greeting` | 10:00 AM | — | Leads with full weekly backlog summary |
| `breakfast_checkin` | 10:30 AM | 1:00 PM | |
| `lunch_checkin` | 1:00 PM | 3:00 PM | |
| `backlog_nudge` | 3:00 PM | 6:00 PM | Workout/chores; references remaining weekly targets |
| `dinner_checkin` | 6:30 PM | 9:00 PM | |
| `eod_checkin` | 9:30 PM | 11:00 PM | Names any outstanding goals explicitly |

**Sunday:**

| Block key | Send at | Notes |
|---|---|---|
| `breakfast_checkin` | 10:00 AM | Includes reminder that noon is grade cutoff |
| `weekly_grade_sun` | 12:00 PM | Grade computed from all completions through 11:59 AM |
| `lunch_checkin` | 1:00 PM | No re-ask pressure — week is closed |
| `dinner_checkin` | 6:30 PM | |
| `weekly_planning_prompt` | 8:00 PM | Prompt to drop next week's weekly_goals.json |

---

### Weekly Goals Input Format

File: `/home/xanderabbott/assistant/data/weekly_goals.json`

```json
{
  "2026-W17": {
    "workout": [
      { "id": "w1", "label": "Morning run",    "target_days": 4 },
      { "id": "w2", "label": "Calisthenics",   "target_days": 3 }
    ],
    "nutrition": [
      { "id": "n1", "label": "Track all meals", "target_days": 7 }
    ],
    "chores": [
      { "id": "c1", "label": "Vacuum living room", "target_days": 1 },
      { "id": "c2", "label": "Grocery run",        "target_days": 1 }
    ],
    "personal": [
      { "id": "p1", "label": "Read 30 min", "target_days": 5 }
    ]
  }
}
```

- `target_days` is a weekly total, not pinned to specific days of the week
- If no entry exists for the current ISO week, start the week with zero goals — do not roll forward from the prior week
- `load_weekly_goals.py` is called by cron Monday 6:00 AM; can also be run manually to seed mid-week

---

### Grading Logic

```
weekly_score = SUM(completions per goal) / SUM(target_days per goal)
             = capped at 1.0 per goal
```

Grade is sent three times per week:

| Cron time | Message key |
|---|---|
| Wednesday 12:00 PM | `weekly_grade_wed` |
| Friday 7:00 PM | `weekly_grade_fri` |
| Sunday 12:00 PM | `weekly_grade_sun` |

`compute_grade.py` determines which key to use based on `datetime.today().weekday()`.

---

### Backlog Pressure

A goal is behind pace when:
```
floor(target_days * (days_elapsed / 7)) > actual_completions
```

Tone escalates by day-of-week. Add a `tone_level` integer (1–4) to `messages.py` lookup:

| Day(s) | Tone level | Prefix style |
|---|---|---|
| Mon–Tue | 1 | Neutral — "You haven't logged [goal] yet this week." |
| Wed | 2 | Mild — "[Goal] is falling behind — you're at the midpoint." |
| Thu–Fri | 3 | Direct — "[Goal] needs N more completions to hit your target." |
| Saturday | 4 | Final — "[Goal] is at risk of not being hit this week. Today is the last chance." |

---

### Extended Crontab (Phase 2 additions)

```cron
# Ingest weekly goals before morning greeting fires
0 6 * * 1 /home/xanderabbott/assistant/venv/bin/python etl/load_weekly_goals.py

# Grades
0 12 * * 3 /home/xanderabbott/assistant/venv/bin/python etl/compute_grade.py
0 19 * * 5 /home/xanderabbott/assistant/venv/bin/python etl/compute_grade.py
0 12 * * 0 /home/xanderabbott/assistant/venv/bin/python etl/compute_grade.py

# Sunday planning prompt
0 20 * * 0 /home/xanderabbott/assistant/venv/bin/python sender.py weekly_planning_prompt

# Nightly response-to-goal linking
30 23 * * * /home/xanderabbott/assistant/venv/bin/python etl/link_responses.py
```

---

## .gitignore Requirements

The following must never be committed:

```
.env
data/data.db
data/weekly_goals.json
__pycache__/
venv/
*.pyc
```

---

## Common Gotchas

- **`data/` subdirectory:** `DB_PATH` is `/home/xanderabbott/assistant/data/data.db` — the extra `data/` folder is real and required. A flat path will silently create a second database in the wrong place.
- **`scp` paths:** Always use full absolute paths. Tilde shorthand behaves unexpectedly over `scp`.
- **`config.py` fallback:** The default fallback for `DB_PATH` still references `/home/pi/assistant/data.db` and needs to be updated to `/home/xanderabbott/assistant/data/data.db`.
- **Timezone:** Pi timezone is set to `America/Chicago`. All cron times in this file are CDT. Verify with `timedatectl` if behavior seems off.
- **systemd after code changes:** After deploying changes to `bot.py` or `config.py`, run `sudo systemctl restart assistant-bot` on the Pi.
- **`poll_interval` in `bot.py`:** `app.run_polling()` must include `poll_interval=5.0`. Do not remove this — it is intentional future-proofing for Phase 3 LLM response latency. The correct call is `app.run_polling(poll_interval=5.0)`.
- **Mid-week first cycle:** Phase 2 may be deployed mid-week. `load_weekly_goals.py` should be safe to run manually at any time to seed the goals table for the current ISO week. Do not add special mid-week bootstrap logic — the standard flow handles it.
