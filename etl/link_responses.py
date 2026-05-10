#!/usr/bin/env python3
"""
Nightly ETL: match this week's unlinked responses to goal labels,
write parsed_value, and insert goal_completions rows with source='scheduled'.
Called by cron at 23:30 daily.

Usage:
    python etl/link_responses.py
"""

import logging
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def get_week_bounds(d: date) -> tuple[str, str]:
    """Return (monday_iso, sunday_iso) for the week containing d."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def find_matching_goals(raw_text: str, goals: list) -> list:
    """Case-insensitive substring match of goal labels in response text."""
    text_lower = raw_text.lower()
    return [g for g in goals if g["label"].lower() in text_lower]


def process_user(user) -> None:
    local_date = datetime.now(ZoneInfo(user["timezone"])).date()
    week_key = db.get_week_key(local_date)
    goals = db.get_goals_for_week(user["id"], week_key)

    if not goals:
        log.info("No goals for user %s (%s) in %s, nothing to link",
                 user["id"], user["name"], week_key)
        return

    week_start, week_end = get_week_bounds(local_date)
    responses = db.get_unlinked_responses_for_week(user["id"], week_start, week_end)

    if not responses:
        log.info("No unlinked responses for user %s (%s) this week",
                 user["id"], user["name"])
        return

    linked = 0
    for row in responses:
        matched = find_matching_goals(row["raw_text"], goals)
        if not matched:
            continue

        db.update_response_parsed_value(row["id"], matched[0]["goal_id"])

        for goal in matched:
            if db.completion_exists_for_response(row["id"], goal["goal_id"], week_key):
                continue
            db.record_goal_completion(
                user["id"],
                day_id=row["day_id"],
                week_key=week_key,
                goal_id=goal["goal_id"],
                response_id=row["id"],
                source="scheduled",
            )
            linked += 1

    log.info("User %s (%s): linked %d completion(s) from %d unlinked response(s)",
             user["id"], user["name"], linked, len(responses))


def main() -> None:
    db.init_db()
    users = db.list_active_users()
    for user in users:
        try:
            process_user(user)
        except Exception as exc:
            log.error("Error linking responses for user id=%s (%s): %s",
                      user["id"], user["name"], exc)


if __name__ == "__main__":
    main()
