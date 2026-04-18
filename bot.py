#!/usr/bin/env python3
"""
Long-running polling bot. Receives user messages and records them.
Run once on boot via systemd.
"""

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


def infer_message_key(text: str) -> str | None:
    """Match proactive entries like 'Breakfast: bagel' to a message key."""
    lower = text.lower()
    for key, prefixes in messages.KEY_PREFIXES.items():
        if any(lower.startswith(p) for p in prefixes):
            return key
    return None


async def handle_done(update: Update, text: str, day_id: int) -> None:
    """Handle 'Done: <goal label>' — write directly to goal_completions."""
    label_query = text[5:].strip()  # strip "done:" prefix
    week_key = db.get_week_key()
    goal = db.get_goal_by_label(week_key, label_query)
    if goal:
        db.record_goal_completion(day_id, week_key, goal["goal_id"], source="proactive")
        log.info("Marked goal '%s' complete via Done: prefix", goal["goal_id"])
        await update.message.reply_text(f"Got it — {goal['label']} marked complete.")
    else:
        await update.message.reply_text(
            "Couldn't match that to a goal this week. "
            "Check your goal labels and try again, or log it manually."
        )


async def handle_message(update: Update, context) -> None:
    text = update.message.text.strip()
    lower = text.lower()
    day_id = db.get_or_create_day()

    # Done: prefix has special routing — goes to goal_completions, not responses
    if lower.startswith("done:"):
        await handle_done(update, text, day_id)
        return

    key = infer_message_key(text)
    if key is None:
        key = db.get_last_sent_key(day_id)

    if key and key != messages.MORNING_GREETING:
        db.record_response(day_id, key, text)
        log.info("Recorded response for '%s': %s", key, text[:60])
        await update.message.reply_text("Got it, recorded.")
    else:
        await update.message.reply_text(
            "Not sure what to log that under.\n"
            "Try starting with: Breakfast, Lunch, Dinner, Workout, Chore, Run, Sleep, or Done."
        )


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
