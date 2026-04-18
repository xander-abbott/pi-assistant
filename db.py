import sqlite3
from datetime import date
import config

DDL = """
CREATE TABLE IF NOT EXISTS days (
    id         INTEGER PRIMARY KEY,
    date       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sent_messages (
    id          INTEGER PRIMARY KEY,
    day_id      INTEGER NOT NULL REFERENCES days(id),
    message_key TEXT NOT NULL,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
    attempt     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS responses (
    id            INTEGER PRIMARY KEY,
    day_id        INTEGER NOT NULL REFERENCES days(id),
    message_key   TEXT NOT NULL,
    raw_text      TEXT NOT NULL,
    parsed_value  TEXT,
    received_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY,
    week_key    TEXT NOT NULL,
    goal_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    label       TEXT NOT NULL,
    target_days INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(week_key, goal_id)
);

CREATE TABLE IF NOT EXISTS goal_completions (
    id           INTEGER PRIMARY KEY,
    day_id       INTEGER NOT NULL REFERENCES days(id),
    week_key     TEXT NOT NULL,
    goal_id      TEXT NOT NULL,
    response_id  INTEGER REFERENCES responses(id),
    source       TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weekly_grades (
    id              INTEGER PRIMARY KEY,
    week_key        TEXT NOT NULL,
    graded_at       TEXT NOT NULL DEFAULT (datetime('now')),
    score_pct       REAL NOT NULL,
    breakdown_json  TEXT NOT NULL,
    message_sent    INTEGER NOT NULL DEFAULT 0
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(DDL)


def get_or_create_day(date_str: str | None = None) -> int:
    if date_str is None:
        date_str = date.today().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO days (date) VALUES (?)", (date_str,)
        )
        row = conn.execute(
            "SELECT id FROM days WHERE date = ?", (date_str,)
        ).fetchone()
        return row["id"]


def record_sent_message(day_id: int, message_key: str, attempt: int = 1) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sent_messages (day_id, message_key, attempt) VALUES (?, ?, ?)",
            (day_id, message_key, attempt),
        )


def record_response(day_id: int, message_key: str, raw_text: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO responses (day_id, message_key, raw_text) VALUES (?, ?, ?)",
            (day_id, message_key, raw_text),
        )


def get_unanswered_keys(day_id: int) -> list[str]:
    """Return message keys that were sent today but have no response yet."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT sm.message_key
            FROM sent_messages sm
            LEFT JOIN responses r
                ON sm.day_id = r.day_id AND sm.message_key = r.message_key
            WHERE sm.day_id = ?
              AND r.id IS NULL
              AND sm.message_key != 'morning_greeting'
            """,
            (day_id,),
        ).fetchall()
        return [r["message_key"] for r in rows]


def get_last_sent_key(day_id: int) -> str | None:
    """Return the most recently sent message key for today (for reply context)."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT message_key FROM sent_messages
            WHERE day_id = ?
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (day_id,),
        ).fetchone()
        return row["message_key"] if row else None


# ---------------------------------------------------------------------------
# Phase 2: weekly goals, completions, grades
# ---------------------------------------------------------------------------

def get_week_key(for_date: date | None = None) -> str:
    """Return ISO week string e.g. '2026-W17'."""
    d = for_date or date.today()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def upsert_goals(week_key: str, goals: list[dict]) -> None:
    """Insert or replace goals for a week. Each dict needs goal_id, category, label, target_days."""
    with _connect() as conn:
        for g in goals:
            conn.execute(
                """
                INSERT INTO goals (week_key, goal_id, category, label, target_days)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(week_key, goal_id) DO UPDATE SET
                    category    = excluded.category,
                    label       = excluded.label,
                    target_days = excluded.target_days
                """,
                (week_key, g["goal_id"], g["category"], g["label"], g["target_days"]),
            )


def get_goals_for_week(week_key: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE week_key = ? ORDER BY category, goal_id",
            (week_key,),
        ).fetchall()


def get_goal_by_label(week_key: str, label: str) -> sqlite3.Row | None:
    """Case-insensitive exact match on label."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE week_key = ? AND lower(label) = lower(?)",
            (week_key, label),
        ).fetchone()


def record_goal_completion(
    day_id: int,
    week_key: str,
    goal_id: str,
    response_id: int | None = None,
    source: str = "proactive",
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO goal_completions (day_id, week_key, goal_id, response_id, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (day_id, week_key, goal_id, response_id, source),
        )


def completion_exists_for_response(response_id: int, goal_id: str, week_key: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM goal_completions
            WHERE response_id = ? AND goal_id = ? AND week_key = ?
            """,
            (response_id, goal_id, week_key),
        ).fetchone()
        return row is not None


def get_completions_for_week(week_key: str, before: str | None = None) -> dict[str, int]:
    """Return {goal_id: count} for a week, optionally capped at a cutoff timestamp."""
    with _connect() as conn:
        if before:
            rows = conn.execute(
                """
                SELECT goal_id, COUNT(*) as cnt
                FROM goal_completions
                WHERE week_key = ? AND completed_at < ?
                GROUP BY goal_id
                """,
                (week_key, before),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT goal_id, COUNT(*) as cnt
                FROM goal_completions
                WHERE week_key = ?
                GROUP BY goal_id
                """,
                (week_key,),
            ).fetchall()
        return {r["goal_id"]: r["cnt"] for r in rows}


def get_behind_goals(week_key: str, days_elapsed: int) -> list[dict]:
    """Return goals that are behind pace given days elapsed in the week (1–7)."""
    goals = get_goals_for_week(week_key)
    completions = get_completions_for_week(week_key)
    behind = []
    for g in goals:
        target = g["target_days"]
        completed = completions.get(g["goal_id"], 0)
        expected = int(target * (days_elapsed / 7))
        if expected > completed:
            behind.append({
                "goal_id":   g["goal_id"],
                "label":     g["label"],
                "category":  g["category"],
                "target":    target,
                "completed": completed,
                "expected":  expected,
                "remaining": target - completed,
            })
    return behind


def get_unlinked_responses_for_week(week_start: str, week_end: str) -> list[sqlite3.Row]:
    """Responses in [week_start, week_end] where parsed_value is still NULL."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT r.id, r.day_id, r.message_key, r.raw_text, d.date
            FROM responses r
            JOIN days d ON r.day_id = d.id
            WHERE d.date >= ? AND d.date <= ?
              AND r.parsed_value IS NULL
              AND r.message_key != 'morning_greeting'
            """,
            (week_start, week_end),
        ).fetchall()


def update_response_parsed_value(response_id: int, parsed_value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE responses SET parsed_value = ? WHERE id = ?",
            (parsed_value, response_id),
        )


def record_weekly_grade(week_key: str, score_pct: float, breakdown_json: str) -> int:
    """Insert a grade row and return its id."""
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO weekly_grades (week_key, score_pct, breakdown_json)
            VALUES (?, ?, ?)
            """,
            (week_key, score_pct, breakdown_json),
        )
        return cur.lastrowid


def mark_grade_sent(grade_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE weekly_grades SET message_sent = 1 WHERE id = ?",
            (grade_id,),
        )
