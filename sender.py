#!/usr/bin/env python3
"""
One-shot message sender. Called by cron.

Usage:
    python sender.py <message_key>
"""

import sys
import asyncio
import logging
from datetime import date

from telegram import Bot

import config
import db
import messages

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

_ESSENTIAL_ORDER = ["breakfast", "lunch", "dinner", "sleep"]

_STATUS_ICONS = {
    "complete":    "✔️",
    "on_track":    "✅",
    "ahead":       "✅",
    "behind":      "⚠️",
    "not_started": "⬜",
}


def _build_morning_report() -> str:
    today = date.today()
    weekday = today.weekday()  # 0=Mon … 6=Sun
    days_elapsed = weekday + 1
    week_key = db.get_week_key()
    week_display = week_key.split("-")[1]  # e.g. "W17"

    all_goals = db.get_all_goals_with_status(week_key, days_elapsed)

    if not all_goals:
        return (
            f"Good morning! No goals loaded for this week ({week_display}).\n"
            f"Drop weekly_goals.json at:\n"
            f"  /home/xanderabbott/assistant/data/weekly_goals.json"
        )

    total_target = sum(g["target"] for g in all_goals)
    total_earned = sum(min(g["completed"], g["target"]) for g in all_goals)
    score_pct = int(total_earned / total_target * 100) if total_target > 0 else 0

    lines = [f"Week score: {score_pct}% — {week_display}", ""]

    for g in all_goals:
        icon = _STATUS_ICONS[g["status"]]
        label = g["label"]
        completed = g["completed"]
        target = g["target"]
        expected = g["expected"]
        status = g["status"]

        if status == "behind":
            detail = f"{completed}/{expected} expected by now (behind)"
        elif status == "complete":
            detail = f"{completed}/{target} (complete)"
        elif status == "not_started":
            detail = f"0/{target} (not started)"
        else:
            detail = f"{completed}/{target} (on track)"

        lines.append(f"{icon} {label} — {detail}")

    return "\n".join(lines)


def _build_eod_checkin() -> str | None:
    day_id = db.get_or_create_day()
    logged = db.get_today_essential_keys(day_id)
    missing = [e.capitalize() for e in _ESSENTIAL_ORDER if e not in logged]

    if not missing:
        return None

    lines = ["Still waiting on a few things today:"]
    for item in missing:
        lines.append(f"  • {item}")
    return "\n".join(lines)


def _build_backlog_nudge() -> str:
    today = date.today()
    weekday = today.weekday()
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


def build_text(key: str) -> str | None:
    if key == messages.MORNING_GREETING:
        return _build_morning_report()
    if key == messages.EOD_CHECKIN:
        return _build_eod_checkin()
    if key == messages.BACKLOG_NUDGE:
        return _build_backlog_nudge()
    return messages.MESSAGE_TEXT.get(key)


async def send_message(key: str) -> None:
    text = build_text(key)
    if text is None:
        log.info("Nothing to send for '%s', skipping", key)
        return
    bot = Bot(token=config.BOT_TOKEN)
    await bot.send_message(chat_id=config.CHAT_ID, text=text)
    day_id = db.get_or_create_day()
    db.record_sent_message(day_id, key, attempt=1)
    log.info("Sent '%s'", key)


def main() -> None:
    db.init_db()

    if len(sys.argv) < 2:
        print("Usage: sender.py <message_key>")
        sys.exit(1)

    arg = sys.argv[1]

    if arg not in messages.MESSAGE_TEXT:
        valid = ", ".join(sorted(messages.MESSAGE_TEXT.keys()))
        print(f"Unknown key '{arg}'. Valid keys: {valid}")
        sys.exit(1)

    asyncio.run(send_message(arg))


if __name__ == "__main__":
    main()
