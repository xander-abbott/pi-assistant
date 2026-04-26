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

**Phase 1 — COMPLETE.** Basic logging, scheduling, re-ask logic.
**Phase 2 — COMPLETE.** Weekly goals, grading, backlog pressure, dynamic morning greeting.
**Phase 2.5 — COMPLETE.** Prefix-only receiving, dynamic morning goal report, dynamic EOD essentials check, re-ask logic removed.

---

## Architecture

- **`sender.py`** — one-shot script called by cron; builds dynamic morning goal report and EOD check-in
- **`bot.py`** — long-running polling process (systemd service); all incoming messages must have a prefix; prefix is the topic; no inference or fallback logic
- **`db.py`** — all SQLite interactions; schema auto-creates on `init_db()`
- **`messages.py`** — all message text, `message_key` constants, goal status templates, tone system
- **`config.py`** — loads `.env`, fails fast if `BOT_TOKEN` or `CHAT_ID` missing
- **`etl/load_weekly_goals.py`** — ingests `weekly_goals.json` into `goals` table; run by cron Monday 6 AM or manually mid-week
- **`etl/compute_grade.py`** — computes weekly score; in Phase 2.5 this is called by the morning report builder, not standalone cron
- **`data/weekly_goals.json`** — user-authored weekly goals file; never committed to git

---

## Key Design Invariants

1. **`message_key` values are permanent.** Stored in the database. Never rename — only add new keys.
2. **`raw_text` is never modified.** Responses stored verbatim. `parsed_value` written separately.
3. **No LLM calls at runtime.** All message generation uses pre-written templates. Must work offline.
4. **Every logged message requires a prefix.** No prefix → no record. No inference, no fallback.
5. **ETL is separate from collection.** Scripts in `etl/` never modify `raw_text` on existing rows.
6. **`DB_PATH` always uses the full absolute path.** Use `/home/xanderabbott/assistant/data/data.db`.

---

## Database Schema (complete)

```sql
-- Phase 1 (unchanged)
days             (id, date, created_at)
sent_messages    (id, day_id, message_key, sent_at, attempt)
responses        (id, day_id, message_key, raw_text, parsed_value, received_at)

-- Phase 2 (unchanged)
goals            (id, week_key, goal_id, category, label, target_days, created_at)
                 UNIQUE(week_key, goal_id)

goal_completions (id, day_id, week_key, goal_id, response_id, source, completed_at)
                 source = 'prefix_match' | 'manual'
                 response_id references responses.id

weekly_grades    (id, week_key, graded_at, score_pct, breakdown_json, message_sent)
```

**Schema change in Phase 2.5:** `goal_completions.source` values change from `'proactive'/'scheduled'` to `'prefix_match'/'manual'`. All completions now originate from prefix-matched responses — there is no longer a separate `Done:` flow.

---

## Message Keys

All defined as constants in `messages.py`. Never rename — stored in database.

**Phase 1 (original):**
```
morning_greeting
breakfast_checkin
lunch_checkin
dinner_checkin
eod_checkin
```

**Phase 2 (added):**
```
sleep_log
weekly_planning_prompt
backlog_nudge
```

**Phase 2.5 changes:**
- `weekly_grade_wed`, `weekly_grade_fri`, `weekly_grade_sun` — removed from cron; grade is now embedded in the daily morning greeting
- `goal_completion` — removed; `Done:` prefix is eliminated entirely
- `eod_checkin` — repurposed; now dynamic, only fires if daily essentials are missing

---

## Prefix Rules (Phase 2.5)

**The fundamental change:** `bot.py` no longer has `infer_message_key()`, `KEY_PREFIXES`, or `get_last_sent_key()`. Every message must contain a `:`. Whatever comes before the `:` is the topic and becomes the `message_key` stored in `responses`.

### Receiving flow in bot.py

```
message received
  → does it contain ':'?
      NO  → reply "Please use a prefix to log entries, e.g. Breakfast: eggs or Workout: 3 mile run"
            do not record anything
      YES → extract prefix (text before ':', stripped, lowercased for matching)
          → write to responses (day_id, message_key=prefix, raw_text=full text)
          → does prefix case-insensitively match any goal label for this week?
              YES → also write to goal_completions (source='prefix_match', response_id=responses.id)
              NO  → no goal_completions row; just a log entry
          → reply "Got it, recorded."
```

### Special numeric parsing for Sleep

When prefix is `sleep`, extract the numeric value from `raw_text` and write it to `parsed_value` on the `responses` row. This enables weekly hour total calculation for sleep goals. Example: `Sleep: 7.5` → `parsed_value = '7.5'`.

### No Done: prefix

`Done:` is eliminated. Logging a prefixed message IS the completion event. There is no separate step.

### No fallback inference

If no `:` is present, nothing is recorded. The bot replies with a prompt to use a prefix. This applies even when the message follows a scheduled check-in question.

---

## Outbound Message Changes (Phase 2.5)

### Morning greeting → Daily goal report

The static morning greeting is replaced entirely with a dynamic daily goal status report. Fires every day (weekdays 7 AM, weekends 10 AM).

**`_build_morning_report()` in sender.py:**
1. Gets current `week_key` and `days_elapsed`
2. Calls `db.get_all_goals_with_status(week_key, days_elapsed)` — returns all goals with `completed`, `expected`, `target`, and a status of `behind` / `on_track` / `ahead` / `complete` / `not_started`
3. Computes current weekly score: `SUM(min(completed, target)) / SUM(target)`
4. Builds message:

```
Week score: 42% — W17

✅ Stretch — 2/5 (on track)
⚠️  Workout — 0/1 expected by now (behind)
✔️ Laundry — 1/1 (complete)
⬜ Groceries — 0/1 (not started)
```

**Status icons:**
- `✅` — on track or ahead
- `⚠️` — behind pace
- `⬜` — not started, not yet behind (day 1 / early week)
- `✔️` — complete (hit target)

**If no goals for the week:** send a simple greeting with a note that no goals are loaded, and remind user to drop `weekly_goals.json`.

**On day 1 (Monday), all goals show `⬜` — this is correct.** No pressure on day 1.

---

### EOD check-in → Dynamic missing essentials alert

The static EOD check-in is replaced with a dynamic check that only fires if daily essentials are missing.

**Daily essentials:** `breakfast`, `lunch`, `dinner`, `sleep` (matched against `responses.message_key` for today)

**`_build_eod_checkin()` in sender.py:**
1. Gets today's `day_id`
2. Queries `responses` for any row today where `message_key` IN (`breakfast`, `lunch`, `dinner`, `sleep`)
3. Determines which are missing
4. If nothing missing → **do not send anything** (exit silently)
5. If any missing → send:

```
Still waiting on a few things today:
  • Breakfast
  • Sleep
```

**Sleep note:** Sleep is checked as a daily essential. It is typically logged the morning after the night in question — this is expected and fine. If not logged by EOD, the bot will remind. User answers the next morning.

---

### Scheduled check-ins (unchanged behavior)

Breakfast, lunch, and dinner check-ins still fire on schedule as prompts. The user responds with a prefix at any time — not necessarily immediately after the prompt.

**Re-ask logic removed.** The `--reask` cron jobs are eliminated. The dynamic EOD check-in replaces mid-day re-ask passes.

---

### Weekly grade (removed from standalone cron)

Grade is now embedded in the daily morning report. Standalone `compute_grade.py` cron jobs (Wed, Fri, Sun) are removed. Grade computation is folded into `_build_morning_report()` or called as a library function.

**Sunday planning prompt (unchanged):** Still fires at 8 PM via cron.
**Saturday backlog nudge (unchanged):** Still fires at 3 PM via cron.

---

## Crontab (Phase 2.5 target state)

```cron
SHELL=/bin/bash
PATH=/home/xanderabbott/assistant/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Daily goal report (replaces morning greeting)
0 7  * * 1-5 cd /home/xanderabbott/assistant && python sender.py morning_greeting >> logs/cron.log 2>&1
0 10 * * 6,0 cd /home/xanderabbott/assistant && python sender.py morning_greeting >> logs/cron.log 2>&1

# Meal check-ins — prompts only
30 8  * * 1-5 cd /home/xanderabbott/assistant && python sender.py breakfast_checkin >> logs/cron.log 2>&1
30 10 * * 6,0 cd /home/xanderabbott/assistant && python sender.py breakfast_checkin >> logs/cron.log 2>&1
0 12  * * *   cd /home/xanderabbott/assistant && python sender.py lunch_checkin >> logs/cron.log 2>&1
0 19  * * *   cd /home/xanderabbott/assistant && python sender.py dinner_checkin >> logs/cron.log 2>&1

# EOD check-in — dynamic; only sends if daily essentials are missing
30 21 * * * cd /home/xanderabbott/assistant && python sender.py eod_checkin >> logs/cron.log 2>&1

# Saturday backlog nudge
0 15 * * 6 cd /home/xanderabbott/assistant && python sender.py backlog_nudge >> logs/cron.log 2>&1

# Weekly goal ingestion (Monday before morning report)
0 6 * * 1 cd /home/xanderabbott/assistant && python etl/load_weekly_goals.py >> logs/cron.log 2>&1

# Sunday planning prompt
0 20 * * 0 cd /home/xanderabbott/assistant && python sender.py weekly_planning_prompt >> logs/cron.log 2>&1
```

**Removed vs Phase 2:**
- `--reask` passes (replaced by dynamic EOD)
- `compute_grade.py` on Wed/Fri/Sun (grade now in morning report)
- `link_responses.py` nightly (linking now happens at write time in bot.py)

---

## Goal Status Logic (db.py)

`get_all_goals_with_status(week_key, days_elapsed)`:
```python
expected = int(target_days * (days_elapsed / 7))
if completed >= target_days:  status = 'complete'
elif completed >= expected:   status = 'on_track'
elif expected == 0:           status = 'not_started'
else:                         status = 'behind'
```

Returns list of dicts with `goal_id`, `label`, `category`, `target`, `completed`, `expected`, `status`.

Backlog tone escalation (Saturday nudge and behind-pace items):
- Mon–Tue → tone 1 (neutral)
- Wed → tone 2 (mild)
- Thu–Fri → tone 3 (direct)
- Sat–Sun → tone 4 (final call)

---

## Weekly Goals File Format

File: `/home/xanderabbott/assistant/data/weekly_goals.json`

```json
{
  "2026-W17": {
    "workout": [
      { "id": "w1", "label": "Workout", "target_days": 3 },
      { "id": "w2", "label": "Stretch", "target_days": 5 }
    ],
    "chores": [
      { "id": "c1", "label": "Laundry",   "target_days": 1 },
      { "id": "c2", "label": "Groceries", "target_days": 1 }
    ]
  }
}
```

- Each new week appended as a new top-level key — old weeks ignored
- `target_days` is a weekly total, not pinned to specific days
- No entry for current week → zero goals, no rollover
- Goal `label` is the prefix match key — keep labels short and unambiguous
- Sleep goals use `target_hours` (weekly total); `parsed_value` on responses holds numeric value for summing

---

## .gitignore Requirements

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

- **`data/` subdirectory:** `DB_PATH` is `/home/xanderabbott/assistant/data/data.db` — flat path silently creates a second database in the wrong place.
- **`scp` paths:** Always use full absolute paths. Tilde shorthand behaves unexpectedly over `scp`.
- **`config.py` fallback:** Default fallback for `DB_PATH` must be `/home/xanderabbott/assistant/data/data.db`, not the old `/home/pi/` path.
- **Timezone:** Pi timezone is `America/Chicago`. All cron times are CDT. Verify with `timedatectl`.
- **systemd after code changes:** After deploying changes to `bot.py` or `config.py`, run `sudo systemctl restart assistant-bot`.
- **`poll_interval` in `bot.py`:** `app.run_polling()` must include `poll_interval=5.0`. Do not remove — intentional future-proofing for Phase 3 LLM latency.
- **Prefix matching:** Goal label matching is case-insensitive. The stored `message_key` in `responses` is the prefix as typed, lowercased. Do not normalize further.
- **Sleep logging is day-offset by design.** `Sleep: 7.5` sent Monday morning refers to Sunday night. The bot checks whether any `sleep` prefix entry exists for today — the offset is expected and requires no special handling.
