import sqlite3
from pathlib import Path
from contextlib import contextmanager
from app.core.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    course_tag TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'capturing',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    audio_path TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS classified_segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    category TEXT NOT NULL,
    summary TEXT,
    inferred_deadline TIMESTAMP,
    source_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_id);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status);
CREATE INDEX IF NOT EXISTS idx_segments_session ON classified_segments(session_id);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_cursor() as conn:
        conn.executescript(SCHEMA)


def get_session(session_id: str):
    with db_cursor() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def get_sessions(limit: int = 50, offset: int = 0):
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [dict(row) for row in rows]


def create_session(session_id: str, course_tag: str, started_at: int):
    with db_cursor() as conn:
        conn.execute(
            "INSERT INTO sessions (id, course_tag, started_at, status) VALUES (?, ?, ?, 'capturing')",
            (session_id, course_tag, started_at)
        )


def update_session_status(session_id: str, status: str, ended_at: int | None = None):
    with db_cursor() as conn:
        if ended_at:
            conn.execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?",
                (status, ended_at, session_id)
            )
        else:
            conn.execute(
                "UPDATE sessions SET status = ? WHERE id = ?",
                (status, session_id)
            )


def create_chunk(chunk_id: str, session_id: str, seq: int, audio_path: str,
                 duration_ms: int, size_bytes: int, started_at: int, ended_at: int):
    with db_cursor() as conn:
        conn.execute(
            """INSERT INTO chunks (id, session_id, seq, audio_path, duration_ms, size_bytes,
               status, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (chunk_id, session_id, seq, audio_path, duration_ms, size_bytes, started_at, ended_at)
        )


def get_chunks(session_id: str):
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE session_id = ? ORDER BY seq",
            (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_pending_chunks():
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def update_chunk_status(chunk_id: str, status: str, error_msg: str | None = None):
    with db_cursor() as conn:
        if error_msg:
            conn.execute(
                "UPDATE chunks SET status = ?, error_msg = ? WHERE id = ?",
                (status, error_msg, chunk_id)
            )
        else:
            conn.execute(
                "UPDATE chunks SET status = ? WHERE id = ?",
                (status, chunk_id)
            )


def create_segment(segment_id: str, session_id: str, start_ms: int, end_ms: int,
                   category: str, summary: str | None, inferred_deadline: int | None, source_text: str):
    with db_cursor() as conn:
        conn.execute(
            """INSERT INTO classified_segments (id, session_id, start_ms, end_ms, category,
               summary, inferred_deadline, source_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (segment_id, session_id, start_ms, end_ms, category, summary, inferred_deadline, source_text)
        )


def get_segments(session_id: str):
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM classified_segments WHERE session_id = ? ORDER BY start_ms",
            (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]