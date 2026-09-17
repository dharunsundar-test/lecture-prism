import sqlite3
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

-- start_ms/end_ms are relative to the start of the segment's chunk.
-- The API converts them to session time using the durations of the preceding chunks.
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

CREATE TABLE IF NOT EXISTS notes (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    doc_type TEXT NOT NULL,
    model TEXT NOT NULL,
    content TEXT NOT NULL,
    generated_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, doc_type)
);

CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_id);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status);
CREATE INDEX IF NOT EXISTS idx_segments_session ON classified_segments(session_id);
"""

# Columns added after the first release; applied to existing databases on startup.
MIGRATIONS = [
    ("sessions", "expected_chunks", "INTEGER"),
    ("chunks", "sha256", "TEXT"),
    ("classified_segments", "chunk_id", "TEXT"),
]


def get_db() -> sqlite3.Connection:
    # The processing worker and request handlers write from different threads.
    conn = sqlite3.connect(settings.db_path, timeout=30)
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
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_segments_chunk ON classified_segments(chunk_id)")


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


def ensure_session(session_id: str, course_tag: str, started_at: int):
    with db_cursor() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, course_tag, started_at, status) VALUES (?, ?, ?, 'capturing')",
            (session_id, course_tag, started_at)
        )


def finalize_session(session_id: str, ended_at: int, expected_chunks: int):
    with db_cursor() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ?, expected_chunks = ? WHERE id = ?",
            (ended_at, expected_chunks, session_id)
        )


def refresh_session_status(session_id: str):
    """Derive session status from its chunks. A session is only done once the phone has
    finalized it and every expected chunk has arrived and been processed."""
    with db_cursor() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            return
        statuses = [r["status"] for r in conn.execute("SELECT status FROM chunks WHERE session_id = ?", (session_id,))]
        expected = session["expected_chunks"]

        if "failed" in statuses:
            status = "failed"
        elif expected is None or len(statuses) < expected:
            status = "capturing"
        elif all(s == "done" for s in statuses):
            status = "done"
        else:
            status = "processing"
        conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))


def upsert_chunk(chunk_id: str, session_id: str, seq: int, audio_path: str, duration_ms: int,
                 size_bytes: int, sha256: str, started_at: int, ended_at: int):
    """Insert a chunk, or replace a previous upload of it and queue it for processing again."""
    with db_cursor() as conn:
        conn.execute(
            """INSERT INTO chunks (id, session_id, seq, audio_path, duration_ms, size_bytes, sha256,
               status, error_msg, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 audio_path = excluded.audio_path, duration_ms = excluded.duration_ms,
                 size_bytes = excluded.size_bytes, sha256 = excluded.sha256, status = 'pending',
                 error_msg = NULL, started_at = excluded.started_at, ended_at = excluded.ended_at""",
            (chunk_id, session_id, seq, audio_path, duration_ms, size_bytes, sha256, started_at, ended_at)
        )


def get_chunk(chunk_id: str):
    with db_cursor() as conn:
        row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return dict(row) if row else None


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


def reset_interrupted_chunks():
    """Chunks left 'processing' by a crash or shutdown would otherwise never be picked up again."""
    with db_cursor() as conn:
        conn.execute("UPDATE chunks SET status = 'pending' WHERE status = 'processing'")


def update_chunk_status(chunk_id: str, status: str, error_msg: str | None = None):
    with db_cursor() as conn:
        conn.execute(
            "UPDATE chunks SET status = ?, error_msg = ? WHERE id = ?",
            (status, error_msg, chunk_id)
        )


def update_chunk_duration(chunk_id: str, duration_ms: int):
    with db_cursor() as conn:
        conn.execute("UPDATE chunks SET duration_ms = ? WHERE id = ?", (duration_ms, chunk_id))


def replace_chunk_segments(chunk_id: str, session_id: str, segments: list[dict]):
    """Swap in a chunk's segments atomically, so reprocessing never leaves duplicates."""
    with db_cursor() as conn:
        conn.execute(
            "DELETE FROM classified_segments WHERE chunk_id = ? OR (chunk_id IS NULL AND id LIKE ?)",
            (chunk_id, f"{chunk_id}_seg_%")
        )
        conn.executemany(
            """INSERT INTO classified_segments (id, session_id, chunk_id, start_ms, end_ms, category,
               summary, inferred_deadline, source_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (f"{chunk_id}_seg_{i}", session_id, chunk_id, s["start_ms"], s["end_ms"], s["category"],
                 s.get("summary"), s.get("inferred_deadline"), s["source_text"])
                for i, s in enumerate(segments)
            ]
        )


def get_segments(session_id: str):
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM classified_segments WHERE session_id = ? ORDER BY start_ms",
            (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def save_notes(session_id: str, documents: list[dict]):
    with db_cursor() as conn:
        conn.execute("DELETE FROM notes WHERE session_id = ?", (session_id,))
        conn.executemany(
            "INSERT INTO notes (session_id, doc_type, model, content, generated_at) VALUES (?, ?, ?, ?, ?)",
            [(session_id, d["type"], d["model"], d["content"], d["generated_at"]) for d in documents]
        )


def get_notes(session_id: str):
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM notes WHERE session_id = ? ORDER BY doc_type", (session_id,)
        ).fetchall()
        return [
            {
                "id": f"{row['session_id']}_{row['doc_type']}",
                "session_id": row["session_id"],
                "type": row["doc_type"],
                "content": row["content"],
                "generated_at": row["generated_at"],
                "model": row["model"],
            }
            for row in rows
        ]
