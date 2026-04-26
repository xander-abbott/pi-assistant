#!/usr/bin/env python3
"""
Long-running polling bot. Receives user messages and records them.
Run once on boot via systemd.
"""

import re
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

import config
import db
import messages

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def _parse_sleep_value(text_after_colon: str) -> str | None:
    m = re.search(r"\d+(?:\.\d+)?", text_after_colon)
    return m.group() if m else None


async def handle_message(update: Update, context) -> None:
    text = update.message.text.strip()

    if ":" not in text:
        await update.message.reply_text(
            "Please use a prefix to log entries, e.g. Breakfast: eggs or Workout: 3 mile run"
        )
        return

    colon_pos = text.index(":")
    prefix = text[:colon_pos].strip().lower()
    day_id = db.get_or_create_day()
    week_key = db.get_week_key()

    parsed_value = None
    if prefix == "sleep":
        parsed_value = _parse_sleep_value(text[colon_pos + 1:])

    response_id = db.record_response(day_id, prefix, text, parsed_value=parsed_value)
    log.info("Recorded response for '%s': %s", prefix, text[:60])

    goal = db.get_goal_by_label(week_key, prefix)
    if goal:
        db.record_goal_completion(
            day_id, week_key, goal["goal_id"],
            response_id=response_id, source="prefix_match"
        )
        log.info("Goal completion recorded for '%s'", goal["goal_id"])

    await update.message.reply_text("Got it, recorded.")


async def handle_start(update: Update, context) -> None:
    await update.message.reply_text(
        f"Assistant bot is running.\nYour chat ID is: {update.effective_chat.id}"
    )


def main() -> None:
    db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started, polling...")
    app.run_polling(poll_interval=5.0)


if __name__ == "__main__":
    main()
