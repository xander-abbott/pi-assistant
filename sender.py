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


async def send_message(key: str, attempt: int = 1) -> None:
    bot = Bot(token=config.BOT_TOKEN)
    text = messages.MESSAGE_TEXT[key] if attempt == 1 else messages.REASK_PREFIX.get(key, messages.MESSAGE_TEXT[key])
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
            # Count prior sends to determine attempt number
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
