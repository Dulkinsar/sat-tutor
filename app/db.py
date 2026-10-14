"""SQLite storage. Students get a random public id and a token (only its hash is stored);
safety events keep no message text.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 4
SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    care_until TEXT,
    profile TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_student ON messages(student_id, id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day);
CREATE TABLE IF NOT EXISTS safety_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    source TEXT NOT NULL,
    categories TEXT NOT NULL
);
"""


def db_path() -> str:
    return os.getenv("DATABASE_PATH", "data/tutor.db")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@contextmanager
def connect():
    path = db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            # v1 addressed students by sequential id with no token; its rows cannot be secured, so drop them.
            conn.executescript("DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS students;")
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(students)")}
        if "care_until" not in columns:
            conn.execute("ALTER TABLE students ADD COLUMN care_until TEXT")
        if "profile" not in columns:
            conn.execute("ALTER TABLE students ADD COLUMN profile TEXT NOT NULL DEFAULT '{}'")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_student(name: str, tags: list[str], profile: dict | None = None) -> dict:
    public_id = "stu_" + secrets.token_urlsafe(9)
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "INSERT INTO students (public_id, token_hash, name, tags, profile) VALUES (?, ?, ?, ?, ?)",
            (public_id, hash_token(token), name, json.dumps(tags), json.dumps(profile or {})),
        )
    return {"id": public_id, "name": name, "tags": tags, "profile": profile or {}, "token": token}


def get_student(public_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, public_id, token_hash, name, tags, profile, care_until FROM students WHERE public_id = ?",
            (public_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "pk": row["id"],
        "id": row["public_id"],
        "token_hash": row["token_hash"],
        "name": row["name"],
        "tags": json.loads(row["tags"]),
        "profile": json.loads(row["profile"]),
        "care_until": row["care_until"],
    }


def set_tags(pk: int, tags: list[str]) -> None:
    with connect() as conn:
        conn.execute("UPDATE students SET tags = ? WHERE id = ?", (json.dumps(tags), pk))


def set_profile(pk: int, profile: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE students SET profile = ? WHERE id = ?", (json.dumps(profile), pk))


def set_care_until(pk: int, until_iso: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE students SET care_until = ? WHERE id = ?", (until_iso, pk))


def delete_student(pk: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM students WHERE id = ?", (pk,))  # messages cascade


def add_turn(pk: int, user_message: str, reply: str) -> None:
    """Store both sides of a turn in one transaction, so history never holds half a turn."""
    with connect() as conn:
        conn.executemany(
            "INSERT INTO messages (student_id, role, content) VALUES (?, ?, ?)",
            [(pk, "user", user_message), (pk, "assistant", reply)],
        )


def history(pk: int, limit: int) -> list[dict]:
    """The most recent `limit` messages, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE student_id = ? ORDER BY id DESC LIMIT ?", (pk, limit)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def message_count(pk: int) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM messages WHERE student_id = ?", (pk,)).fetchone()[0]


def log_usage(model: str, tokens_in: int, tokens_out: int, usd: float) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO usage (day, model, tokens_in, tokens_out, usd) VALUES (?, ?, ?, ?, ?)",
            (today(), model, tokens_in, tokens_out, usd),
        )


def spend_on(day: str) -> float:
    with connect() as conn:
        return conn.execute("SELECT COALESCE(SUM(usd), 0) FROM usage WHERE day = ?", (day,)).fetchone()[0]


def usage_by_day(days: int = 14) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT day, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
            "ROUND(SUM(usd), 6) AS usd FROM usage GROUP BY day ORDER BY day DESC LIMIT ?",
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


def log_safety_event(source: str, categories: list[str]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO safety_events (day, source, categories) VALUES (?, ?, ?)",
            (today(), source, json.dumps(sorted(categories))),
        )


def safety_by_day(days: int = 14) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT day, COUNT(*) AS events FROM safety_events GROUP BY day ORDER BY day DESC LIMIT ?", (days,)
        ).fetchall()
    return [dict(r) for r in rows]
