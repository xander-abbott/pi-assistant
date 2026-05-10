#!/usr/bin/env python3
"""
Compute weekly grade and send Telegram message.
Called by cron: Wed 12PM, Fri 7PM, Sun 12PM.

Usage:
    python etl/compute_grade.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Bot

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
import db
import messages

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def compute_grade(user_id: int, week_key: str, before: str | None = None) -> tuple[float, dict]:
    """
    Returns (score_pct, breakdown_by_category).
    score_pct = sum(min(completions, target) per goal) / sum(target per goal).
    before: UTC datetime string in SQLite format; only count completions before this timestamp.
    """
    goals = db.get_goals_for_week(user_id, week_key)
    if not goals:
        return 0.0, {}

    completions = db.get_completions_for_week(user_id, week_key, before=before)

    categories: dict[str, dict] = {}
    total_target = 0
    total_completed = 0

    for g in goals:
        cat = g["category"]
        gid = g["goal_id"]
        target = g["target_days"]
        completed = min(completions.get(gid, 0), target)

        if cat not in categories:
            categories[cat] = {"target": 0, "completed": 0}
        categories[cat]["target"] += target
        categories[cat]["completed"] += completed
        total_target += target
        total_completed += completed

    score_pct = total_completed / total_target if total_target > 0 else 0.0

    breakdown = {
        cat: {
            "target":    vals["target"],
            "completed": vals["completed"],
            "pct":       vals["completed"] / vals["target"] if vals["target"] > 0 else 0.0,
        }
        for cat, vals in categories.items()
    }

    return score_pct, breakdown


def format_grade_message(week_key: str, score_pct: float, breakdown: dict, msg_key: str) -> str:
    pct_str = f"{score_pct * 100:.0f}%"
    lines = [f"Weekly grade ({week_key}): {pct_str}\n"]

    for cat, vals in sorted(breakdown.items()):
        cat_pct = f"{vals['pct'] * 100:.0f}%"
        lines.append(f"  {cat.capitalize()}: {vals['completed']}/{vals['target']} ({cat_pct})")

    if msg_key == messages.WEEKLY_GRADE_WED:
        lines.append("\nMidweek check — keep it up!")
    elif msg_key == messages.WEEKLY_GRADE_FRI:
        lines.append("\nTwo more days to hit your targets!")
    elif msg_key == messages.WEEKLY_GRADE_SUN:
        lines.append("\nWeek is closed. Time to plan next week!")

    return "\n".join(lines)


async def send_grade(user, week_key: str, msg_key: str, cutoff: str | None = None) -> None:
    score_pct, breakdown = compute_grade(user["id"], week_key, before=cutoff)
    breakdown_json = json.dumps(breakdown)

    grade_id = db.record_weekly_grade(user["id"], week_key, score_pct, breakdown_json)
    text = format_grade_message(week_key, score_pct, breakdown, msg_key)

    bot = Bot(token=config.BOT_TOKEN)
    await bot.send_message(chat_id=user["chat_id"], text=text)

    db.mark_grade_sent(grade_id)

    day_id = db.get_or_create_day(user["id"])
    db.record_sent_message(day_id, msg_key)


def compute_grade_args_for_user(user, message_key: str) -> tuple[str, str | None]:
    """Return (week_key, cutoff) for the given user and grade message_key.

    cutoff is a UTC datetime string in SQLite format ("YYYY-MM-DD HH:MM:SS") for
    the Sunday noon cutoff, or None for Wednesday/Friday (all completions count).
    """
    local_date = datetime.now(ZoneInfo(user["timezone"])).date()
    week_key = db.get_week_key(local_date)
    cutoff = None
    if message_key == messages.WEEKLY_GRADE_SUN:
        cutoff_local = datetime(local_date.year, local_date.month, local_date.day,
                                12, 0, tzinfo=ZoneInfo(user["timezone"]))
        cutoff = cutoff_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return week_key, cutoff


def process_user(user) -> None:
    local_date = datetime.now(ZoneInfo(user["timezone"])).date()
    weekday = local_date.weekday()

    if weekday == 2:
        msg_key = messages.WEEKLY_GRADE_WED
    elif weekday == 4:
        msg_key = messages.WEEKLY_GRADE_FRI
    elif weekday == 6:
        msg_key = messages.WEEKLY_GRADE_SUN
    else:
        log.info("compute_grade: weekday=%d is not a grade day for user %s (%s), skipping",
                 weekday, user["id"], user["name"])
        return

    week_key, cutoff = compute_grade_args_for_user(user, msg_key)
    asyncio.run(send_grade(user, week_key, msg_key, cutoff=cutoff))
    log.info("Grade sent for user %s (%s), week %s", user["id"], user["name"], week_key)


def main() -> None:
    db.init_db()
    users = db.list_active_users()
    for user in users:
        try:
            process_user(user)
        except Exception as exc:
            log.error("Error computing grade for user id=%s (%s): %s",
                      user["id"], user["name"], exc)


if __name__ == "__main__":
    main()
