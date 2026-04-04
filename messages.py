MORNING_GREETING  = "morning_greeting"
BREAKFAST_CHECKIN = "breakfast_checkin"
LUNCH_CHECKIN     = "lunch_checkin"
DINNER_CHECKIN    = "dinner_checkin"
EOD_CHECKIN       = "eod_checkin"

# Order drives re-ask priority
DAILY_SEQUENCE = [
    MORNING_GREETING,
    BREAKFAST_CHECKIN,
    LUNCH_CHECKIN,
    DINNER_CHECKIN,
    EOD_CHECKIN,
]

# Keyword prefixes for proactive user messages ("Breakfast: ...")
KEY_PREFIXES = {
    BREAKFAST_CHECKIN: ["breakfast"],
    LUNCH_CHECKIN:     ["lunch"],
    DINNER_CHECKIN:    ["dinner"],
    EOD_CHECKIN:       ["workout", "chore", "gym", "run", "calisthenics", "eod"],
}

# If no response by this hour (24h), the re-ask job will re-send the checkin
EXPECTED_BY_HOUR = {
    BREAKFAST_CHECKIN: 11,
    LUNCH_CHECKIN:     14,
    DINNER_CHECKIN:    20,
    EOD_CHECKIN:       22,
}

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
}

REASK_PREFIX = {
    BREAKFAST_CHECKIN: "Still waiting on this one — what did you have for breakfast?",
    LUNCH_CHECKIN:     "Still waiting — what did you have for lunch?",
    DINNER_CHECKIN:    "Still waiting — what did you eat for dinner?",
    EOD_CHECKIN:       "Still waiting — any workouts or chores to log for today?",
}
