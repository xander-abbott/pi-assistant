#!/usr/bin/env python3
"""
One-shot message sender. Called by cron.

Usage:
    python sender.py <message_key>          # send a specific message
    python sender.py --reask                # re-send any unanswered checkins due by now
"""

import sys
import asyncio
import logging
from datetime import datetime, date

from telegram import Bot

import config
import db
import messages

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def _build_morning_greeting() -> str:
    today = date.today()
    weekday = today.weekday()  # 0=Mon … 6=Sun
    days_elapsed = weekday + 1
    week_key = db.get_week_key()
    behind = db.get_behind_goals(week_key, days_elapsed)
    base = messages.MESSAGE_TEXT[messages.MORNING_GREETING]

    if not behind:
        return base

    tone_level = messages.get_tone_level(weekday)
    nudge_lines = [messages.format_backlog_item(g, tone_level) for g in behind]

    if weekday == 5:  # Saturday — lead with full backlog
        backlog_section = "\n".join(f"  • {line}" for line in nudge_lines)
        return f"Weekly backlog:\n\n{backlog_section}\n\n{base}"
    else:
        backlog_section = "\n".join(f"  • {line}" for line in nudge_lines)
        return f"{base}\n\nBacklog check:\n{backlog_section}"


def _build_backlog_nudge() -> str:
    today = date.today()
    weekday = today.weekday()  # Should be Saturday (5)
    days_elapsed = weekday + 1
    week_key = db.get_week_key()
    behind = db.get_behind_goals(week_key, days_elapsed)

    if not behind:
        return "You're all caught up on your weekly goals — great work!"

    tone_level = messages.get_tone_level(weekday)
    lines = ["Weekly targets — remaining work:"]
    for g in behind:
        lines.append(f"  • {messages.format_backlog_item(g, tone_level)}")
    return "\n".join(lines)


def build_text(key: str, attempt: int) -> str:
    if attempt > 1:
        return messages.REASK_PREFIX.get(key, messages.MESSAGE_TEXT[key])
    if key == messages.MORNING_GREETING:
        return _build_morning_greeting()
    if key == messages.BACKLOG_NUDGE:
        return _build_backlog_nudge()
    return messages.MESSAGE_TEXT[key]


async def send_message(key: str, attempt: int = 1) -> None:
    bot = Bot(token=config.BOT_TOKEN)
    text = build_text(key, attempt)
    await bot.send_message(chat_id=config.CHAT_ID, text=text)
    day_id = db.get_or_create_day()
    db.record_sent_message(day_id, key, attempt=attempt)
    log.info("Sent '%s' (attempt %d)", key, attempt)


async def reask() -> None:
    now_hour = datetime.now().hour
    day_id = db.get_or_create_day()
    unanswered = db.get_unanswered_keys(day_id)

    for key in unanswered:
        expected_by = messages.EXPECTED_BY_HOUR.get(key)
        if expected_by is not None and now_hour >= expected_by:
            log.info("Re-asking '%s'", key)
            await send_message(key, attempt=2)


def main() -> None:
    db.init_db()

    if len(sys.argv) < 2:
        print("Usage: sender.py <message_key> | --reask")
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--reask":
        asyncio.run(reask())
    elif arg in messages.MESSAGE_TEXT:
        asyncio.run(send_message(arg))
    else:
        valid = ", ".join(messages.MESSAGE_TEXT.keys())
        print(f"Unknown key '{arg}'. Valid keys: {valid}")
        sys.exit(1)


if __name__ == "__main__":
    main()
