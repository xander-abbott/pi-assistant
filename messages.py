MORNING_GREETING  = "morning_greeting"
BREAKFAST_CHECKIN = "breakfast_checkin"
LUNCH_CHECKIN     = "lunch_checkin"
DINNER_CHECKIN    = "dinner_checkin"
EOD_CHECKIN       = "eod_checkin"

# Phase 2
SLEEP_LOG              = "sleep_log"
WEEKLY_GRADE_WED       = "weekly_grade_wed"
WEEKLY_GRADE_FRI       = "weekly_grade_fri"
WEEKLY_GRADE_SUN       = "weekly_grade_sun"
WEEKLY_PLANNING_PROMPT = "weekly_planning_prompt"
BACKLOG_NUDGE          = "backlog_nudge"
GOAL_COMPLETION        = "goal_completion"  # kept for DB compatibility; Done: prefix removed in Phase 2.5

MESSAGE_TEXT = {
    MORNING_GREETING: (
        "Good morning! Here's your to-do list for today:\n\n"
        "  - [ ] Fill in your tasks here\n\n"
        "Have a great day!"
    ),
    BREAKFAST_CHECKIN: "What did you have for breakfast?",
    LUNCH_CHECKIN:     "What did you have for lunch?",
    DINNER_CHECKIN:    "Dinner time — what did you eat?",
    EOD_CHECKIN:       (
        "End of day check-in:\n"
        "What workouts or chores did you get done today?"
    ),
    SLEEP_LOG: "How many hours did you sleep last night?",
    WEEKLY_GRADE_WED:  "Midweek grade — see below.",
    WEEKLY_GRADE_FRI:  "Friday grade — see below.",
    WEEKLY_GRADE_SUN:  "Weekly grade — see below.",
    WEEKLY_PLANNING_PROMPT: (
        "Time to plan next week!\n\n"
        "Drop your weekly_goals.json at:\n"
        "  /home/xanderabbott/assistant/data/weekly_goals.json\n\n"
        "Goals load automatically Monday at 6 AM, or run:\n"
        "  python etl/load_weekly_goals.py"
    ),
    BACKLOG_NUDGE: "Weekly backlog — see below.",
}

# ---------------------------------------------------------------------------
# Phase 2: backlog pressure tone
# ---------------------------------------------------------------------------

BACKLOG_TONE = {
    1: "You haven't logged {label} yet this week.",
    2: "{label} is falling behind — you're at the midpoint.",
    3: "{label} needs {remaining} more completion(s) to hit your target.",
    4: "{label} is at risk of not being hit this week. Today is the last chance.",
}


def get_tone_level(weekday: int) -> int:
    """Return tone level 1–4 based on weekday (0=Mon … 6=Sun)."""
    if weekday <= 1:    return 1  # Mon–Tue
    elif weekday == 2:  return 2  # Wed
    elif weekday <= 4:  return 3  # Thu–Fri
    else:               return 4  # Sat


def format_backlog_item(goal: dict, tone_level: int) -> str:
    template = BACKLOG_TONE[tone_level]
    return template.format(label=goal["label"], remaining=goal["remaining"])
