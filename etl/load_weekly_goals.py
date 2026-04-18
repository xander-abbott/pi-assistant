#!/usr/bin/env python3
"""
Ingest weekly_goals.json into the goals table.
Called by cron Monday 6:00 AM. Safe to run manually mid-week to seed goals.

Usage:
    python etl/load_weekly_goals.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db

GOALS_FILE = "/home/xanderabbott/assistant/data/weekly_goals.json"


def main() -> None:
    db.init_db()
    week_key = db.get_week_key()

    if not os.path.exists(GOALS_FILE):
        print(f"No goals file found at {GOALS_FILE}. Skipping.")
        return

    with open(GOALS_FILE) as f:
        all_goals = json.load(f)

    if week_key not in all_goals:
        print(f"No entry for {week_key} in goals file. Skipping.")
        return

    week_data = all_goals[week_key]
    goals_flat: list[dict] = []
    for category, items in week_data.items():
        for item in items:
            goals_flat.append({
                "goal_id":    item["id"],
                "category":   category,
                "label":      item["label"],
                "target_days": item["target_days"],
            })

    db.upsert_goals(week_key, goals_flat)
    print(f"Loaded {len(goals_flat)} goals for {week_key}")


if __name__ == "__main__":
    main()
