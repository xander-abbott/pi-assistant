#!/usr/bin/env python3
"""
Message sender — tick loop and manual send.

Usage:
    python sender.py tick                               # cron entrypoint (every 15 min)
    python sender.py send <message_key> --user <name>  # manual send
    SENDER_SELFTEST=1 python sender.py                 # developer self-test (requires /tmp/data_copy_chunk_a.db)
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Bot

import config
import db
import messages
import schedule as schedule_module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "etl"))
import compute_grade

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

_ESSENTIAL_ORDER = ["breakfast", "lunch", "dinner", "sleep"]

_GRADE_KEYS = {messages.WEEKLY_GRADE_WED, messages.WEEKLY_GRADE_FRI, messages.WEEKLY_GRADE_SUN}

_STATUS_ICONS = {
    "complete":    "✔️",
    "on_track":    "✅",
    "ahead":       "✅",
    "behind":      "⚠️",
    "not_started": "⬜",
}


# ---------------------------------------------------------------------------
# Dynamic message builders
# ---------------------------------------------------------------------------

def _build_morning_report(user) -> str:
    local_date = datetime.now(ZoneInfo(user["timezone"])).date()
    weekday = local_date.weekday()
    days_elapsed = weekday + 1
    week_key = db.get_week_key(local_date)
    week_display = week_key.split("-")[1]

    all_goals = db.get_all_goals_with_status(user["id"], week_key, days_elapsed)

    if not all_goals:
        return (
            f"Good morning! No goals loaded for this week ({week_display}).\n"
            f"Drop your goals file at:\n"
            f"  /home/xanderabbott/assistant/data/weekly_goals/{user['id']}.json"
        )

    total_target = sum(g["target"] for g in all_goals)
    total_earned = sum(min(g["completed"], g["target"]) for g in all_goals)
    score_pct = int(total_earned / total_target * 100) if total_target > 0 else 0

    lines = [f"Week score: {score_pct}% — {week_display}", ""]

    for g in all_goals:
        icon = _STATUS_ICONS[g["status"]]
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

        lines.append(f"{icon} {g['label']} — {detail}")

    return "\n".join(lines)


def _build_eod_checkin(user) -> str | None:
    day_id = db.get_or_create_day(user["id"])
    logged = db.get_today_essential_keys(day_id)
    missing = [e.capitalize() for e in _ESSENTIAL_ORDER if e not in logged]

    if not missing:
        return None

    lines = ["Still waiting on a few things today:"]
    for item in missing:
        lines.append(f"  • {item}")
    return "\n".join(lines)


def _build_backlog_nudge(user) -> str:
    local_date = datetime.now(ZoneInfo(user["timezone"])).date()
    weekday = local_date.weekday()
    days_elapsed = weekday + 1
    week_key = db.get_week_key(local_date)
    behind = db.get_behind_goals(user["id"], week_key, days_elapsed)

    if not behind:
        return "You're all caught up on your weekly goals — great work!"

    tone_level = messages.get_tone_level(weekday)
    lines = ["Weekly targets — remaining work:"]
    for g in behind:
        lines.append(f"  • {messages.format_backlog_item(g, tone_level)}")
    return "\n".join(lines)


def build_text(key: str, user) -> str | None:
    if key == messages.MORNING_GREETING:
        return _build_morning_report(user)
    if key == messages.EOD_CHECKIN:
        return _build_eod_checkin(user)
    if key == messages.BACKLOG_NUDGE:
        return _build_backlog_nudge(user)
    return messages.MESSAGE_TEXT.get(key)


# ---------------------------------------------------------------------------
# Telegram send (async, no DB side-effects)
# ---------------------------------------------------------------------------

async def _telegram_send(chat_id: int, text: str) -> None:
    bot = Bot(token=config.BOT_TOKEN)
    await bot.send_message(chat_id=chat_id, text=text)


# ---------------------------------------------------------------------------
# Tick loop — primary cron entrypoint
# ---------------------------------------------------------------------------

def tick() -> None:
    # Capture a single consistent timestamp for the entire tick.
    now_utc = datetime.now(timezone.utc)
    users = db.list_active_users()

    total_fired = 0
    total_already_sent = 0
    total_not_due = 0

    for user in users:
        try:
            local_date = now_utc.astimezone(ZoneInfo(user["timezone"])).date()
            day_id = db.get_or_create_day(user["id"])
            events = schedule_module.get_events_for_user(user, local_date)

            for message_key, scheduled_local_time in events:
                if schedule_module.should_fire(
                    user, message_key, scheduled_local_time, now_utc, db
                ):
                    if message_key in _GRADE_KEYS:
                        # Grade path: compute_grade.send_grade handles send + DB record internally.
                        week_key, cutoff = compute_grade.compute_grade_args_for_user(
                            user, message_key
                        )
                        try:
                            asyncio.run(
                                compute_grade.send_grade(user, week_key, message_key, cutoff=cutoff)
                            )
                        except Exception as send_exc:
                            log.error(
                                "Grade send failed for user=%s key=%s: %s — will retry",
                                user["id"], message_key, send_exc,
                            )
                            continue
                    else:
                        text = build_text(message_key, user)
                        if text is None:
                            log.info(
                                "user=%s key=%s: should_fire=True but nothing to send, skipping",
                                user["id"], message_key,
                            )
                            continue

                        try:
                            asyncio.run(_telegram_send(user["chat_id"], text))
                        except Exception as send_exc:
                            log.error(
                                "Send failed for user=%s key=%s: %s — not recording, will retry",
                                user["id"], message_key, send_exc,
                            )
                            continue

                        try:
                            db.record_sent_message(day_id, message_key)
                        except Exception as rec_exc:
                            log.error(
                                "ALERT: send succeeded but DB record FAILED for user=%s key=%s: %s",
                                user["id"], message_key, rec_exc,
                            )

                    total_fired += 1

                else:
                    # Determine why it didn't fire for the summary counter.
                    if db.was_message_sent(day_id, message_key):
                        total_already_sent += 1
                    else:
                        total_not_due += 1

        except Exception as exc:
            log.error(
                "Unexpected error processing user id=%s (%s): %s",
                user["id"], user["name"], exc, exc_info=True,
            )

    total_skipped = total_already_sent + total_not_due
    log.info(
        "tick: checked %d users, fired %d, skipped %d (already-sent: %d, not-due: %d)",
        len(users), total_fired, total_skipped, total_already_sent, total_not_due,
    )


# ---------------------------------------------------------------------------
# Manual send — secondary entrypoint for testing
# ---------------------------------------------------------------------------

def cmd_send(message_key: str, user_name: str) -> None:
    user = db.get_user_by_name(user_name)
    if user is None:
        log.error("No user with name=%r", user_name)
        sys.exit(1)

    text = build_text(message_key, user)
    if text is None:
        log.info("Nothing to send for key=%r (empty message)", message_key)
        return

    asyncio.run(_telegram_send(user["chat_id"], text))
    day_id = db.get_or_create_day(user["id"])
    db.record_sent_message(day_id, message_key)
    log.info("Sent '%s' to user '%s'", message_key, user_name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    db.init_db()

    parser = argparse.ArgumentParser(description="Pi assistant message sender")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("tick", help="Run the scheduler tick (called by cron every 15 min)")

    send_parser = subparsers.add_parser("send", help="Manually send one message to one user")
    send_parser.add_argument("message_key", help="Key from messages.py (e.g. morning_greeting)")
    send_parser.add_argument("--user", required=True, dest="user_name",
                             help="User name as stored in DB (e.g. xander)")

    args = parser.parse_args()

    if args.command == "tick":
        tick()
    elif args.command == "send":
        cmd_send(args.message_key, args.user_name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.environ.get("SENDER_SELFTEST") != "1":
        main()
    else:
        TEST_DB = "/tmp/data_copy_chunk_a.db"

        if not os.path.exists(TEST_DB):
            print(f"Test DB not found at {TEST_DB}. Run the migration first.")
            sys.exit(1)

        # Override paths before any DB call.
        config.DB_PATH = TEST_DB
        # Prevent accidental real Telegram sends; any attempted send will fail
        # gracefully inside the per-send exception handlers.
        config.BOT_TOKEN = "test-mode-no-sends"

        db.init_db()

        # 1. compute_grade_args_for_user: Wed/Fri keys → cutoff=None
        _test_user = {"id": 1, "timezone": "America/Chicago", "name": "xander"}
        for _key in (messages.WEEKLY_GRADE_WED, messages.WEEKLY_GRADE_FRI):
            _wk, _co = compute_grade.compute_grade_args_for_user(_test_user, _key)
            assert _co is None, f"cutoff should be None for {_key}, got {_co!r}"
        print("PASS: compute_grade_args_for_user returns cutoff=None for Wed/Fri")

        # 2. compute_grade_args_for_user: Sun key → SQLite-format UTC string (space-separated)
        _wk, _co = compute_grade.compute_grade_args_for_user(_test_user, messages.WEEKLY_GRADE_SUN)
        assert _co is not None, "cutoff should not be None for Sunday"
        assert " " in _co and "T" not in _co, f"cutoff must use SQLite space format, got {_co!r}"
        assert _co.endswith(":00"), f"cutoff should be on-the-minute, got {_co!r}"
        print(f"PASS: compute_grade_args_for_user returns SQLite-format cutoff for Sun: {_co!r}")

        # 3. tick() runs without crashing; grade routing included
        print(f"Running tick() against {TEST_DB} ...")
        try:
            tick()
            print("PASS: tick() completed without crashing (grade routing active)")
        except Exception as e:
            print(f"FAIL: tick() raised {e}")
            sys.exit(1)
