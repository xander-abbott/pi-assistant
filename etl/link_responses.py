#!/usr/bin/env python3
"""
Nightly ETL: match this week's unlinked responses to goal labels,
write parsed_value, and insert goal_completions rows with source='scheduled'.
Called by cron at 23:30 daily.

Usage:
    python etl/link_responses.py
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db


def get_week_bounds(d: date | None = None) -> tuple[str, str]:
    """Return (monday_iso, sunday_iso) for the week containing d."""
    d = d or date.today()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def find_matching_goals(raw_text: str, goals: list) -> list:
    """Case-insensitive substring match of goal labels in response text."""
    text_lower = raw_text.lower()
    return [g for g in goals if g["label"].lower() in text_lower]


def main() -> None:
    db.init_db()
    week_key = db.get_week_key()
    goals = db.get_goals_for_week(week_key)

    if not goals:
        print(f"No goals for {week_key}. Nothing to link.")
        return

    week_start, week_end = get_week_bounds()
    responses = db.get_unlinked_responses_for_week(week_start, week_end)

    if not responses:
        print("No unlinked responses this week.")
        return

    linked = 0
    for row in responses:
        matched = find_matching_goals(row["raw_text"], goals)
        if not matched:
            continue

        # Use the first match for parsed_value; insert a completion per match
        db.update_response_parsed_value(row["id"], matched[0]["goal_id"])

        for goal in matched:
            if db.completion_exists_for_response(row["id"], goal["goal_id"], week_key):
                continue
            db.record_goal_completion(
                day_id=row["day_id"],
                week_key=week_key,
                goal_id=goal["goal_id"],
                response_id=row["id"],
                source="scheduled",
            )
            linked += 1

    print(f"Linked {linked} completion(s) from {len(responses)} unlinked response(s).")


if __name__ == "__main__":
    main()
