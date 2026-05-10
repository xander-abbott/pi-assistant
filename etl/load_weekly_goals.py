#!/usr/bin/env python3
"""
Ingest per-user weekly goals JSON files into the goals table.
Called by cron Monday 6:00 AM. Safe to run manually mid-week to seed goals.

Usage:
    python etl/load_weekly_goals.py

Goals files: /home/xanderabbott/assistant/data/weekly_goals/<user_id>.json
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

GOALS_DIR  = "/home/xanderabbott/assistant/data/weekly_goals"
LEGACY_PATH = "/home/xanderabbott/assistant/data/weekly_goals.json"


def load_for_user(user, week_key: str) -> None:
    goals_file = os.path.join(GOALS_DIR, f"{user['id']}.json")

    if not os.path.exists(goals_file):
        log.info("No goals file for user %s (%s), skipping", user["id"], user["name"])
        return

    with open(goals_file) as f:
        all_goals = json.load(f)

    if week_key not in all_goals:
        log.info("No entry for %s in goals file for user %s (%s), skipping",
                 week_key, user["id"], user["name"])
        return

    goals_flat: list[dict] = []
    for category, items in all_goals[week_key].items():
        for item in items:
            goals_flat.append({
                "goal_id":     item["id"],
                "category":    category,
                "label":       item["label"],
                "target_days": item["target_days"],
            })

    db.upsert_goals(user["id"], week_key, goals_flat)
    log.info("Loaded %d goals for user %s (%s), week %s",
             len(goals_flat), user["id"], user["name"], week_key)


def main() -> None:
    db.init_db()

    if os.path.exists(LEGACY_PATH):
        print(
            f"Legacy weekly_goals.json detected at {LEGACY_PATH}. "
            f"Multi-user expects data/weekly_goals/<user_id>.json. "
            f"Move the file to data/weekly_goals/1.json to continue using it."
        )

    week_key = db.get_week_key()
    users = db.list_active_users()

    for user in users:
        try:
            load_for_user(user, week_key)
        except Exception as exc:
            log.error("Error loading goals for user id=%s (%s): %s",
                      user["id"], user["name"], exc)


if __name__ == "__main__":
    main()
