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


def load_for_user(user, week_key: str, goals_file: str | None = None) -> None:
    if goals_file is None:
        goals_file = os.path.join(GOALS_DIR, f"{user['id']}.json")

    if not os.path.exists(goals_file):
        log.info("No goals file for user %s (%s), skipping", user["id"], user["name"])
        return

    with open(goals_file) as f:
        all_goals = json.load(f)

    if week_key not in all_goals:
        # ISO week keys ("YYYY-Www") sort correctly as strings.
        prior_weeks = sorted(k for k in all_goals.keys() if k < week_key)
        if not prior_weeks:
            log.warning("No previous week to roll forward for user %s (%s), skipping",
                        user["id"], user["name"])
            return

        prior_week_key = prior_weeks[-1]
        all_goals[week_key] = all_goals[prior_week_key]

        with open(goals_file, "w") as f:
            json.dump(all_goals, f, indent=2)

        log.info("Auto-rolled goals from %s as %s for user %s (%s)",
                 prior_week_key, week_key, user["id"], user["name"])

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
    if os.environ.get("LOAD_GOALS_SELFTEST") == "1":
        import tempfile

        # Capture db.upsert_goals calls instead of hitting the DB.
        upsert_calls: list[tuple] = []
        db.upsert_goals = lambda user_id, week_key, goals: upsert_calls.append(
            (user_id, week_key, list(goals))
        )

        user = {"id": 999, "name": "testuser"}
        current_week = "2026-W21"
        prior_week = "2026-W20"
        older_week = "2026-W18"

        sample_week_entries = {
            "fitness": [
                {"id": "run_3x",   "label": "Run 3x",        "target_days": 3},
                {"id": "lift_2x",  "label": "Lift 2x",       "target_days": 2},
            ],
            "study": [
                {"id": "read_30m", "label": "Read 30m",      "target_days": 5},
            ],
        }
        prior_week_entries = {
            "fitness": [
                {"id": "walk_5x",  "label": "Walk 5x",       "target_days": 5},
            ],
        }

        # ----- Scenario i: current week present -----
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({current_week: sample_week_entries}, tf)
            scenario_i_path = tf.name

        upsert_calls.clear()
        load_for_user(user, current_week, goals_file=scenario_i_path)
        assert len(upsert_calls) == 1, "scenario i: upsert_goals should be called once"
        _, called_week, called_goals = upsert_calls[0]
        assert called_week == current_week, \
            f"scenario i: week mismatch ({called_week})"
        assert len(called_goals) == 3, \
            f"scenario i: expected 3 flat goals, got {len(called_goals)}"
        # File should be unchanged (no roll-forward triggered).
        with open(scenario_i_path) as f:
            disk = json.load(f)
        assert list(disk.keys()) == [current_week], \
            f"scenario i: file should still only have current_week, got {list(disk.keys())}"
        os.unlink(scenario_i_path)
        print("PASS: scenario i — current week present, loads as before")

        # ----- Scenario ii: current week missing, prior weeks exist (roll forward) -----
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            # Multiple prior weeks; latest prior should win.
            json.dump({older_week: sample_week_entries, prior_week: prior_week_entries}, tf)
            scenario_ii_path = tf.name

        upsert_calls.clear()
        load_for_user(user, current_week, goals_file=scenario_ii_path)
        assert len(upsert_calls) == 1, "scenario ii: upsert_goals should be called once"
        _, called_week, called_goals = upsert_calls[0]
        assert called_week == current_week, \
            f"scenario ii: should upsert under current_week, got {called_week}"
        # prior_week_entries has 1 goal (walk_5x). After roll-forward we should see it.
        assert len(called_goals) == 1, \
            f"scenario ii: expected 1 rolled goal, got {len(called_goals)}"
        assert called_goals[0]["goal_id"] == "walk_5x", \
            f"scenario ii: should roll forward from prior_week, got {called_goals[0]['goal_id']}"
        # File should now contain the rolled-forward entry under current_week.
        with open(scenario_ii_path) as f:
            disk = json.load(f)
        assert current_week in disk, "scenario ii: file should now have current_week"
        assert disk[current_week] == prior_week_entries, \
            "scenario ii: current_week entry should equal prior week's entries"
        os.unlink(scenario_ii_path)
        print("PASS: scenario ii — current week missing, auto-rolls from latest prior")

        # ----- Scenario iii: no prior weeks (genuinely first-time) -----
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({}, tf)
            scenario_iii_path = tf.name

        upsert_calls.clear()
        load_for_user(user, current_week, goals_file=scenario_iii_path)
        assert upsert_calls == [], \
            f"scenario iii: upsert_goals should NOT be called, got {upsert_calls}"
        # File should be untouched.
        with open(scenario_iii_path) as f:
            disk = json.load(f)
        assert disk == {}, f"scenario iii: file should remain empty, got {disk}"
        os.unlink(scenario_iii_path)
        print("PASS: scenario iii — no prior weeks, logs warning and skips")

        # ----- Scenario iv: no file at all -----
        upsert_calls.clear()
        nonexistent_path = "/tmp/__load_goals_selftest_does_not_exist__.json"
        if os.path.exists(nonexistent_path):
            os.unlink(nonexistent_path)
        load_for_user(user, current_week, goals_file=nonexistent_path)
        assert upsert_calls == [], \
            f"scenario iv: upsert_goals should NOT be called, got {upsert_calls}"
        assert not os.path.exists(nonexistent_path), \
            "scenario iv: missing file should not be created"
        print("PASS: scenario iv — no file at all, logs warning and skips")

        # ----- Scenario v: prior week present where some keys are *later* alphabetically
        # than current_week (proves we filter by k < current). -----
        future_week = "2026-W30"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({prior_week: prior_week_entries, future_week: sample_week_entries}, tf)
            scenario_v_path = tf.name

        upsert_calls.clear()
        load_for_user(user, current_week, goals_file=scenario_v_path)
        assert len(upsert_calls) == 1, "scenario v: upsert_goals should be called once"
        _, _, called_goals = upsert_calls[0]
        assert called_goals[0]["goal_id"] == "walk_5x", \
            f"scenario v: should roll from prior_week (not future_week), got {called_goals[0]['goal_id']}"
        os.unlink(scenario_v_path)
        print("PASS: scenario v — only prior weeks (k < current_week) considered for roll-forward")

        print("\nAll tests passed.")
        raise SystemExit(0)

    main()
