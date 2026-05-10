import os
from datetime import time, datetime
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Schedule definitions
# ---------------------------------------------------------------------------
# morning_greeting uses a weekday/weekend split. All other events are a single
# time.Time. WEEKLY_EVENTS fire only on the specified day_of_week (Python
# weekday convention: Monday=0, Sunday=6).

SCHEDULES = {
    "default": {
        "morning_greeting": {"weekday": time(7, 0), "weekend": time(10, 0)},
        "breakfast_checkin": time(9, 0),
        "lunch_checkin": time(12, 0),
        "dinner_checkin": time(19, 0),
        "eod_checkin": time(21, 0),
    },
}

WEEKLY_EVENTS = {
    "default": {
        "weekly_grade_wed": {"day_of_week": 2, "time": time(12, 0)},  # Wednesday noon local
        "weekly_grade_fri": {"day_of_week": 4, "time": time(19, 0)},  # Friday 7 PM local
        "weekly_grade_sun": {"day_of_week": 6, "time": time(12, 0)},  # Sunday noon local
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_events_for_user(user, local_date):
    """Return [(message_key, scheduled_local_time), ...] for user on local_date.

    Resolves morning_greeting's weekday/weekend split using local_date.weekday().
    Includes WEEKLY_EVENTS entries only on their matching day_of_week.
    """
    profile = user["schedule_profile"]
    is_weekend = local_date.weekday() >= 5  # 5=Saturday, 6=Sunday

    events = []
    for key, value in SCHEDULES[profile].items():
        if isinstance(value, dict) and "weekday" in value:
            t = value["weekend"] if is_weekend else value["weekday"]
        else:
            t = value
        events.append((key, t))

    for key, spec in WEEKLY_EVENTS[profile].items():
        if local_date.weekday() == spec["day_of_week"]:
            events.append((key, spec["time"]))

    return events


def should_fire(user, message_key, scheduled_local_time, now_utc, db_module,
                tick_window_minutes=15):
    """Return True if message_key should be sent to user right now.

    Args:
        user: db row with 'id', 'timezone', 'schedule_profile'.
        message_key: the key to check (e.g. 'morning_greeting').
        scheduled_local_time: datetime.time in the user's local timezone.
        now_utc: timezone-aware datetime in UTC (typically datetime.now(UTC)).
        db_module: module (or mock) exposing get_or_create_day and was_message_sent.
        tick_window_minutes: fire if within [0, window) minutes of scheduled time.
    """
    local_now = now_utc.astimezone(ZoneInfo(user["timezone"]))
    scheduled_today_local = datetime.combine(
        local_now.date(),
        scheduled_local_time,
        tzinfo=ZoneInfo(user["timezone"]),
    )
    delta_min = (local_now - scheduled_today_local).total_seconds() / 60
    if not (0 <= delta_min < tick_window_minutes):
        return False

    day_id = db_module.get_or_create_day(user["id"])
    if db_module.was_message_sent(day_id, message_key):
        return False

    return True


# ---------------------------------------------------------------------------
# Self-test (python schedule.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.environ.get("SCHEDULE_SELFTEST") != "1":
        print("schedule.py has no CLI. Run with SCHEDULE_SELFTEST=1 for tests.")
        raise SystemExit(0)

    from datetime import date, timedelta, timezone
    from types import SimpleNamespace

    def _mock_db(sent: bool) -> SimpleNamespace:
        return SimpleNamespace(
            get_or_create_day=lambda user_id: 1,
            was_message_sent=lambda day_id, key: sent,
        )

    # Xander: America/Chicago (CDT = UTC-5 in May 2026)
    # Mom:    America/Los_Angeles (PDT = UTC-7 in May 2026)
    xander = {"id": 1, "timezone": "America/Chicago",     "schedule_profile": "default"}
    mom    = {"id": 2, "timezone": "America/Los_Angeles", "schedule_profile": "default"}

    # 2026-05-11 is a Monday.
    # 07:00 CDT = 12:00 UTC   (Chicago)
    # 07:00 PDT = 14:00 UTC   (LA)
    # So 12:00 UTC = 07:00 CDT = 05:00 PDT.
    target_time = time(7, 0)
    t0 = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)  # exactly 07:00 CDT

    db_clear = _mock_db(sent=False)
    db_sent  = _mock_db(sent=True)

    # 1. Exactly at scheduled time → True
    assert should_fire(xander, "morning_greeting", target_time, t0, db_clear), \
        "exactly at scheduled time should fire"
    print("PASS: exactly at scheduled time → True")

    # 2. 14 minutes after → True (still within window)
    assert should_fire(xander, "morning_greeting", target_time, t0 + timedelta(minutes=14), db_clear), \
        "14 min after should fire"
    print("PASS: 14 minutes after → True")

    # 3. 16 minutes after → False (outside window)
    assert not should_fire(xander, "morning_greeting", target_time, t0 + timedelta(minutes=16), db_clear), \
        "16 min after should not fire"
    print("PASS: 16 minutes after → False")

    # 4. 1 minute before → False (delta is negative)
    assert not should_fire(xander, "morning_greeting", target_time, t0 - timedelta(minutes=1), db_clear), \
        "1 min before should not fire"
    print("PASS: 1 minute before → False")

    # 5. Already sent → False
    assert not should_fire(xander, "morning_greeting", target_time, t0, db_sent), \
        "already sent should not fire"
    print("PASS: already sent → False")

    # 6. Same now_utc: fires for Chicago (07:00 CDT), not for LA (05:00 PDT)
    assert should_fire(xander, "morning_greeting", target_time, t0, db_clear), \
        "Chicago 07:00 CDT should fire"
    assert not should_fire(mom, "morning_greeting", target_time, t0, db_clear), \
        "LA 05:00 PDT should not fire for 07:00 event"
    print("PASS: same now_utc fires for Chicago (07:00 CDT) but not LA (05:00 PDT)")

    # 7. get_events_for_user: weekday morning = 07:00, no grade keys on Monday
    monday = date(2026, 5, 11)
    weekday_events = dict(get_events_for_user(xander, monday))
    assert weekday_events["morning_greeting"] == time(7, 0), \
        f"weekday morning should be 07:00, got {weekday_events['morning_greeting']}"
    for grade_key in ("weekly_grade_wed", "weekly_grade_fri", "weekly_grade_sun"):
        assert grade_key not in weekday_events, \
            f"{grade_key} should not appear on Monday"
    print("PASS: weekday morning_greeting = 07:00, no grade keys on Monday")

    # 8. get_events_for_user: weekend morning = 10:00, weekly_grade_sun present on Sunday
    sunday = date(2026, 5, 10)
    sunday_events = dict(get_events_for_user(xander, sunday))
    assert sunday_events["morning_greeting"] == time(10, 0), \
        f"weekend morning should be 10:00, got {sunday_events['morning_greeting']}"
    assert "weekly_grade_sun" in sunday_events, \
        "weekly_grade_sun should appear on Sunday"
    assert sunday_events["weekly_grade_sun"] == time(12, 0), \
        f"weekly_grade_sun should fire at 12:00, got {sunday_events['weekly_grade_sun']}"
    for absent_key in ("weekly_grade_wed", "weekly_grade_fri"):
        assert absent_key not in sunday_events, \
            f"{absent_key} should not appear on Sunday"
    print("PASS: weekend morning_greeting = 10:00, weekly_grade_sun at 12:00 on Sunday only")

    # 9. get_events_for_user: weekly_grade_wed present on Wednesday, no fri/sun grade
    wednesday = date(2026, 5, 13)
    wed_events = dict(get_events_for_user(xander, wednesday))
    assert "weekly_grade_wed" in wed_events, \
        "weekly_grade_wed should appear on Wednesday"
    assert wed_events["weekly_grade_wed"] == time(12, 0), \
        f"weekly_grade_wed should fire at 12:00, got {wed_events['weekly_grade_wed']}"
    for absent_key in ("weekly_grade_fri", "weekly_grade_sun"):
        assert absent_key not in wed_events, \
            f"{absent_key} should not appear on Wednesday"
    print("PASS: weekly_grade_wed at 12:00 on Wednesday, no fri/sun grade")

    # 10. get_events_for_user: weekly_grade_fri present on Friday, no wed/sun grade
    friday = date(2026, 5, 15)
    fri_events = dict(get_events_for_user(xander, friday))
    assert "weekly_grade_fri" in fri_events, \
        "weekly_grade_fri should appear on Friday"
    assert fri_events["weekly_grade_fri"] == time(19, 0), \
        f"weekly_grade_fri should fire at 19:00, got {fri_events['weekly_grade_fri']}"
    for absent_key in ("weekly_grade_wed", "weekly_grade_sun"):
        assert absent_key not in fri_events, \
            f"{absent_key} should not appear on Friday"
    print("PASS: weekly_grade_fri at 19:00 on Friday, no wed/sun grade")

    print("\nAll tests passed.")
