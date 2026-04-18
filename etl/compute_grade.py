#!/usr/bin/env python3
"""
Compute weekly grade and send Telegram message.
Called by cron: Wed 12PM, Fri 7PM, Sun 12PM.

Usage:
    python etl/compute_grade.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, date

from telegram import Bot

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
import db
import messages


def compute_grade(week_key: str, before: str | None = None) -> tuple[float, dict]:
    """
    Returns (score_pct, breakdown_by_category).
    score_pct = sum(min(completions, target) per goal) / sum(target per goal), capped at 1.0.
    before: optional ISO datetime string; only count completions before this timestamp.
    """
    goals = db.get_goals_for_week(week_key)
    if not goals:
        return 0.0, {}

    completions = db.get_completions_for_week(week_key, before=before)

    categories: dict[str, dict] = {}
    total_target = 0
    total_completed = 0

    for g in goals:
        cat = g["category"]
        gid = g["goal_id"]
        target = g["target_days"]
        completed = min(completions.get(gid, 0), target)  # cap per-goal at target

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


async def send_grade(week_key: str, msg_key: str, cutoff: str | None = None) -> None:
    score_pct, breakdown = compute_grade(week_key, before=cutoff)
    breakdown_json = json.dumps(breakdown)

    grade_id = db.record_weekly_grade(week_key, score_pct, breakdown_json)
    text = format_grade_message(week_key, score_pct, breakdown, msg_key)

    bot = Bot(token=config.BOT_TOKEN)
    await bot.send_message(chat_id=config.CHAT_ID, text=text)

    db.mark_grade_sent(grade_id)

    day_id = db.get_or_create_day()
    db.record_sent_message(day_id, msg_key)


def main() -> None:
    db.init_db()
    today = date.today()
    weekday = today.weekday()  # 0=Mon … 6=Sun
    week_key = db.get_week_key()

    cutoff = None
    if weekday == 2:    # Wednesday
        msg_key = messages.WEEKLY_GRADE_WED
    elif weekday == 4:  # Friday
        msg_key = messages.WEEKLY_GRADE_FRI
    elif weekday == 6:  # Sunday — only count completions before noon
        msg_key = messages.WEEKLY_GRADE_SUN
        cutoff = datetime.combine(today, datetime.min.time().replace(hour=12)).isoformat()
    else:
        print(f"compute_grade.py called on unexpected weekday {weekday}; defaulting to wed key.")
        msg_key = messages.WEEKLY_GRADE_WED

    asyncio.run(send_grade(week_key, msg_key, cutoff=cutoff))


if __name__ == "__main__":
    main()
