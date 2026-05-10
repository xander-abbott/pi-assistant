#!/usr/bin/env python3
"""
migrate_multitenant.py — one-time migration to multi-tenant schema.

Usage:
    python migrate_multitenant.py /path/to/data.db [--env-file /path/to/.env]

Safe to run twice: checks for existing state before each step.
Wraps everything in a single transaction; rolls back on any error.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def load_chat_id(env_file: Path) -> int:
    """Parse CHAT_ID from a .env file without requiring python-dotenv."""
    if not env_file.exists():
        sys.exit(f"ERROR: env file not found: {env_file}")

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "CHAT_ID":
            value = value.strip().strip('"').strip("'")
            if not value:
                sys.exit("ERROR: CHAT_ID is empty in env file")
            try:
                return int(value)
            except ValueError:
                sys.exit(f"ERROR: CHAT_ID is not an integer: {value!r}")

    sys.exit("ERROR: CHAT_ID not found in env file")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def print_summary(conn: sqlite3.Connection) -> None:
    for table, sample_sql in [
        ("users",         "SELECT * FROM users LIMIT 1"),
        ("days",          "SELECT id, user_id, date FROM days ORDER BY id LIMIT 1"),
        ("goals",         "SELECT id, user_id, week_key, goal_id FROM goals ORDER BY id LIMIT 1"),
        ("weekly_grades", "SELECT id, user_id, week_key FROM weekly_grades ORDER BY id LIMIT 1"),
    ]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<16}: {count} rows")
        sample = conn.execute(sample_sql).fetchone()
        if sample:
            print(f"  {table:<16}  sample: {dict(sample)}")


def migrate(db_path: Path, chat_id: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Enable WAL so a crash mid-migration doesn't corrupt the DB.
    conn.execute("PRAGMA journal_mode=WAL")
    # Must be OFF before BEGIN so DROP TABLE + RENAME doesn't falsely flag
    # sent_messages/responses FKs as broken mid-transaction. We run
    # PRAGMA foreign_key_check inside the transaction instead, then restore ON.
    conn.execute("PRAGMA foreign_keys=OFF")

    print("=== Before migration ===")
    if table_exists(conn, "users"):
        print(f"  users table : exists ({conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]} rows)")
    else:
        print("  users table : not present")

    days_count_before = conn.execute("SELECT COUNT(*) FROM days").fetchone()[0]
    print(f"  days        : {days_count_before} rows, "
          f"user_id column {'present' if column_exists(conn, 'days', 'user_id') else 'NOT present'}")

    goals_count_before = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
    print(f"  goals       : {goals_count_before} rows, "
          f"user_id column {'present' if column_exists(conn, 'goals', 'user_id') else 'NOT present'}")

    grades_count_before = conn.execute("SELECT COUNT(*) FROM weekly_grades").fetchone()[0]
    print(f"  weekly_grades: {grades_count_before} rows, "
          f"user_id column {'present' if column_exists(conn, 'weekly_grades', 'user_id') else 'NOT present'}")

    try:
        conn.execute("BEGIN")

        # ------------------------------------------------------------------ #
        # Step 1: create users table                                          #
        # ------------------------------------------------------------------ #
        if not table_exists(conn, "users"):
            print("\n[1/5] Creating users table...")
            conn.execute("""
                CREATE TABLE users (
                    id               INTEGER PRIMARY KEY,
                    name             TEXT    NOT NULL,
                    chat_id          INTEGER NOT NULL UNIQUE,
                    timezone         TEXT    NOT NULL,
                    schedule_profile TEXT    NOT NULL DEFAULT 'default',
                    active           INTEGER NOT NULL DEFAULT 1,
                    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            print("      Done.")
        else:
            print("\n[1/5] users table already exists — skipping CREATE.")

        # ------------------------------------------------------------------ #
        # Step 2: insert Xander as user_id=1                                 #
        # ------------------------------------------------------------------ #
        existing_user = conn.execute(
            "SELECT id FROM users WHERE id = 1"
        ).fetchone()

        if existing_user is None:
            print(f"\n[2/5] Inserting Xander (chat_id={chat_id})...")
            conn.execute("""
                INSERT INTO users (id, name, chat_id, timezone, schedule_profile, active)
                VALUES (1, 'xander', ?, 'America/Chicago', 'default', 1)
            """, (chat_id,))
            print("      Done.")
        else:
            print("\n[2/5] user_id=1 already exists — skipping INSERT.")
            existing_chat_id = conn.execute(
                "SELECT chat_id FROM users WHERE id = 1"
            ).fetchone()[0]
            if existing_chat_id != chat_id:
                print(f"      WARNING: existing chat_id={existing_chat_id} "
                      f"differs from env CHAT_ID={chat_id}. Not updating.")

        # ------------------------------------------------------------------ #
        # Step 3 + 4: recreate days with user_id column                      #
        #                                                                     #
        # SQLite cannot ADD a NOT NULL FK column to an existing table, so we  #
        # use the create-new-table-copy-drop-rename pattern.                  #
        # ------------------------------------------------------------------ #
        if column_exists(conn, "days", "user_id"):
            print("\n[3/5] days.user_id already present — skipping recreate.")
        else:
            days_before = conn.execute("SELECT COUNT(*) FROM days").fetchone()[0]
            print(f"\n[3/5] Recreating days table ({days_before} existing rows)...")

            conn.execute("""
                CREATE TABLE days_new (
                    id         INTEGER PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    date       TEXT    NOT NULL,
                    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(user_id, date)
                )
            """)

            # Copy existing rows, backfilling user_id=1 for all of them.
            conn.execute("""
                INSERT INTO days_new (id, user_id, date, created_at)
                SELECT id, 1, date, created_at
                FROM days
            """)

            days_after = conn.execute("SELECT COUNT(*) FROM days_new").fetchone()[0]
            if days_after != days_before:
                raise RuntimeError(
                    f"Row count mismatch after copy: before={days_before}, after={days_after}"
                )

            conn.execute("DROP TABLE days")
            conn.execute("ALTER TABLE days_new RENAME TO days")
            print(f"      Copied {days_after} rows with user_id=1. Done.")

        # ------------------------------------------------------------------ #
        # Step 4: recreate goals with user_id column                         #
        # ------------------------------------------------------------------ #
        if column_exists(conn, "goals", "user_id"):
            print("\n[4/5] goals.user_id already present — skipping recreate.")
        else:
            goals_before = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
            print(f"\n[4/5] Recreating goals table ({goals_before} existing rows)...")

            conn.execute("""
                CREATE TABLE goals_new (
                    id          INTEGER PRIMARY KEY,
                    user_id     INTEGER NOT NULL REFERENCES users(id),
                    week_key    TEXT    NOT NULL,
                    goal_id     TEXT    NOT NULL,
                    category    TEXT    NOT NULL,
                    label       TEXT    NOT NULL,
                    target_days INTEGER NOT NULL,
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(user_id, week_key, goal_id)
                )
            """)

            conn.execute("""
                INSERT INTO goals_new (id, user_id, week_key, goal_id, category, label, target_days, created_at)
                SELECT id, 1, week_key, goal_id, category, label, target_days, created_at
                FROM goals
            """)

            goals_after = conn.execute("SELECT COUNT(*) FROM goals_new").fetchone()[0]
            if goals_after != goals_before:
                raise RuntimeError(
                    f"goals row count mismatch: before={goals_before}, after={goals_after}"
                )

            conn.execute("DROP TABLE goals")
            conn.execute("ALTER TABLE goals_new RENAME TO goals")
            print(f"      Copied {goals_after} rows with user_id=1. Done.")

        # ------------------------------------------------------------------ #
        # Step 5: recreate weekly_grades with user_id column                 #
        # ------------------------------------------------------------------ #
        if column_exists(conn, "weekly_grades", "user_id"):
            print("\n[5/5] weekly_grades.user_id already present — skipping recreate.")
        else:
            grades_before = conn.execute("SELECT COUNT(*) FROM weekly_grades").fetchone()[0]
            print(f"\n[5/5] Recreating weekly_grades table ({grades_before} existing rows)...")

            conn.execute("""
                CREATE TABLE weekly_grades_new (
                    id             INTEGER PRIMARY KEY,
                    user_id        INTEGER NOT NULL REFERENCES users(id),
                    week_key       TEXT    NOT NULL,
                    graded_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                    score_pct      REAL    NOT NULL,
                    breakdown_json TEXT    NOT NULL,
                    message_sent   INTEGER NOT NULL DEFAULT 0
                )
            """)

            conn.execute("""
                INSERT INTO weekly_grades_new (id, user_id, week_key, graded_at, score_pct, breakdown_json, message_sent)
                SELECT id, 1, week_key, graded_at, score_pct, breakdown_json, message_sent
                FROM weekly_grades
            """)

            grades_after = conn.execute("SELECT COUNT(*) FROM weekly_grades_new").fetchone()[0]
            if grades_after != grades_before:
                raise RuntimeError(
                    f"weekly_grades row count mismatch: before={grades_before}, after={grades_after}"
                )

            conn.execute("DROP TABLE weekly_grades")
            conn.execute("ALTER TABLE weekly_grades_new RENAME TO weekly_grades")
            print(f"      Copied {grades_after} rows with user_id=1. Done.")

        # Verify referential integrity across ALL tables before committing.
        # PRAGMA foreign_key_check returns one row per violation; any row is a bug.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            details = "; ".join(
                f"{r[0]}.rowid={r[1]} → {r[2]}" for r in violations
            )
            raise RuntimeError(f"FK violations detected before commit: {details}")

        conn.execute("COMMIT")
        # Restore FK enforcement for any subsequent use of this connection.
        conn.execute("PRAGMA foreign_keys=ON")

    except Exception as exc:
        conn.execute("ROLLBACK")
        print(f"\nERROR: {exc}")
        print("Transaction rolled back. Database is unchanged.")
        conn.close()
        sys.exit(1)

    print("\n=== After migration ===")
    print_summary(conn)

    # Sanity checks — row counts and FK integrity for all migrated tables.
    errors = []

    for table, count_before in [
        ("days",          days_count_before),
        ("goals",         goals_count_before),
        ("weekly_grades", grades_count_before),
    ]:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if total != count_before:
            errors.append(f"{table} row count changed: {count_before} → {total} (expected no change)")

        orphaned = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id NOT IN (SELECT id FROM users)"
        ).fetchone()[0]
        if orphaned:
            errors.append(f"{orphaned} {table} rows have no matching user")

    if errors:
        print("\nSANITY CHECK FAILURES:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\nSanity checks passed.")

    conn.close()
    print("\nMigration complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-tenant migration for pi-assistant")
    parser.add_argument("db_path", help="Path to SQLite database file")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file (default: .env next to this script)",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        sys.exit(f"ERROR: database not found: {db_path}")

    env_file = Path(args.env_file) if args.env_file else Path(__file__).parent / ".env"
    chat_id = load_chat_id(env_file)

    print(f"Database : {db_path.resolve()}")
    print(f"Env file : {env_file.resolve()}")
    print(f"CHAT_ID  : {chat_id}")

    migrate(db_path, chat_id)


if __name__ == "__main__":
    main()
