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
