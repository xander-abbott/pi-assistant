# Pi Productivity Assistant

A Raspberry Pi-based productivity assistant that communicates via Telegram. Tracks daily meals, workouts, chores, and to-dos. Not an AI agent — pre-determined message flows only (Phase 1).

## Project Phases

### Phase 1 (current) — Basic structured messaging
- Scheduled morning greeting (7am weekdays, 10am weekends)
- Scheduled meal check-ins (breakfast, lunch, dinner)
- End-of-day workout/chore check-in
- Re-ask logic for unanswered questions
- Proactive user messages ("Breakfast: bagel") recorded anytime
- All responses stored in SQLite, ETL builds daily data points

### Phase 2 (future) — AI agent layer
Not yet designed. Will build on Phase 1 data.

## Deployment Target

Raspberry Pi running headlessly at home. Assumed username `pi`, project lives at `/home/pi/assistant`.

## Architecture

- **`sender.py`** — one-shot script called by cron to send scheduled messages
- **`bot.py`** — long-running polling process (runs as systemd service) to receive user replies
- **`db.py`** — all SQLite interactions; schema auto-creates on first run
- **`messages.py`** — all message text and message keys in one place; edit here to change what the bot says
- **`config.py`** — loads `.env`, fails fast if `BOT_TOKEN` or `CHAT_ID` missing
- **`scheduler/crontab.example`** — ready-to-install crontab
- **`systemd/assistant-bot.service`** — systemd unit for the polling bot
- **`etl/daily_summary.py`** — Phase 2 stub, not yet implemented

## Key Design Decisions

- **Polling, not webhooks** — avoids needing a public URL or ngrok on the Pi
- **Cron for scheduling, systemd for the always-on bot** — cron fires one-shot senders; systemd keeps the receiver alive
- **Split processes** — sender and receiver are separate; a crash in one doesn't kill the other
- **`message_key` as the join concept** — ties sent messages to responses across `sent_messages` and `responses` tables
- **ETL is separate from collection** — `raw_text` is stored verbatim; `parsed_value` is written later so the parser can be fixed retroactively

## Database Schema

Three tables in SQLite:
- `days` — one row per calendar date
- `sent_messages` — every message the bot sends, keyed by `message_key` and `day_id`
- `responses` — every reply from the user, matched to a `message_key`

`get_unanswered_keys(day_id)` LEFT JOINs sent vs responses to find gaps — drives re-ask logic.

## Weekend Goal Status

- [x] Project structure created
- [x] `sender.py`, `bot.py`, `db.py`, `messages.py`, `config.py` written
- [x] Crontab and systemd service files ready
- [ ] Deploy to Pi (copy files, create `.env`, install deps)
- [ ] Create Telegram bot via BotFather, get BOT_TOKEN + CHAT_ID
- [ ] Test `python sender.py morning_greeting` manually
- [ ] Install crontab, verify first scheduled message fires

## Setup Checklist (for fresh Pi deploy)

```bash
cp .env.example .env
# fill in BOT_TOKEN and CHAT_ID

python -m venv venv
venv/bin/pip install -r requirements.txt

# Test sender manually
venv/bin/python sender.py morning_greeting

# Install cron
crontab scheduler/crontab.example

# Install and start the polling bot
sudo cp systemd/assistant-bot.service /etc/systemd/system/
sudo systemctl enable assistant-bot
sudo systemctl start assistant-bot
```

## Getting Your Chat ID

1. Message your bot `/start` in Telegram
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `chat.id` in the response — put it in `.env` as `CHAT_ID`

## What NOT to Change Without Care

- The `message_key` string values in `messages.py` are stored in the database — renaming them orphans historical data
- `DB_PATH` in `.env` defaults to `/home/pi/assistant/data.db`; keep it on a path that survives reboots
