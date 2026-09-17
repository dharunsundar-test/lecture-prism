import hashlib
import os
import re
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Literal, Optional, List
from pathlib import Path
from app.core.config import settings
from app.core.pairing import require_phone_or_local
from app.db.database import (
    ensure_session, finalize_session, refresh_session_status, upsert_chunk, get_chunk,
    get_session, get_sessions, get_chunks, get_segments, save_notes, get_notes
)
from app.services.audio import build_session_audio, chunk_offsets
from app.services.notes import NotesError, generate_documents

health_router = APIRouter()
router = APIRouter(prefix="/api", dependencies=[Depends(require_phone_or_local)])

# Session ids become directory names, so keep them to a safe character set.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


class ChunkUploadResponse(BaseModel):
    chunk_id: str
    status: str


class SegmentResponse(BaseModel):
    id: str
    session_id: str
    start_ms: int
    end_ms: int
    category: str
    summary: Optional[str]
    inferred_deadline: Optional[int]
    source_text: str


class SessionResponse(BaseModel):
    id: str
    course_tag: str
    started_at: int
    ended_at: Optional[int]
    expected_chunks: Optional[int]
    status: str
    chunks: list


class FinalizeRequest(BaseModel):
    ended_at: int
    chunk_count: int
    course_tag: str
    started_at: int


class NotesRequest(BaseModel):
    model: Literal["local", "groq"] = "local"


class NotesResponse(BaseModel):
    documents: List[dict]


@health_router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/ping", response_model=HealthResponse)
async def ping():
    """Like /health, but only succeeds with a valid pair token, so the phone can tell
    'PC unreachable' apart from 'paired with a different PC'."""
    return HealthResponse()


def _check_session_id(session_id: str):
    if not SESSION_ID_RE.match(session_id):
        raise HTTPException(400, "Invalid session id")


@router.post("/chunks/upload", response_model=ChunkUploadResponse)
def upload_chunk(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    seq: int = Form(..., ge=0),
    course_tag: str = Form(...),
    started_at: int = Form(...),
    ended_at: int = Form(...),
    session_started_at: Optional[int] = Form(None),
    sha256: Optional[str] = Form(None),
):
    """Idempotent: the phone retries whenever it doesn't see a response, so the same chunk
    can arrive more than once."""
    _check_session_id(session_id)
    ensure_session(session_id, course_tag, session_started_at or started_at)

    chunk_id = f"{session_id}_chunk_{seq}"
    audio_dir = settings.audio_dir / session_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"chunk_{seq}.m4a"
    partial_path = audio_dir / f"chunk_{seq}.m4a.partial"

    digest = hashlib.sha256()
    size_bytes = 0
    with open(partial_path, "wb") as out:
        while block := audio.file.read(1024 * 1024):
            digest.update(block)
            out.write(block)
            size_bytes += len(block)
    actual_sha = digest.hexdigest()

    if size_bytes == 0 or (sha256 and sha256.lower() != actual_sha):
        partial_path.unlink(missing_ok=True)
        raise HTTPException(400, "Empty upload" if size_bytes == 0 else "Checksum mismatch")

    existing = get_chunk(chunk_id)
    if existing and existing["sha256"] == actual_sha and audio_path.exists():
        # Retry of a chunk we already have: don't touch the file, it may be mid-processing.
        partial_path.unlink(missing_ok=True)
        return ChunkUploadResponse(chunk_id=chunk_id, status=existing["status"])

    os.replace(partial_path, audio_path)
    upsert_chunk(chunk_id, session_id, seq, str(audio_path), ended_at - started_at,
                 size_bytes, actual_sha, started_at, ended_at)
    refresh_session_status(session_id)
    return ChunkUploadResponse(chunk_id=chunk_id, status="pending")


@router.post("/sessions/{session_id}/finalize", response_model=SessionResponse)
def finalize(session_id: str, request: FinalizeRequest):
    """Sent by the phone when recording stops. Order-independent with chunk uploads."""
    _check_session_id(session_id)
    ensure_session(session_id, request.course_tag, request.started_at)
    finalize_session(session_id, request.ended_at, request.chunk_count)
    refresh_session_status(session_id)
    return _session_response(get_session(session_id))


def _session_response(session: dict) -> SessionResponse:
    return SessionResponse(
        id=session["id"],
        course_tag=session["course_tag"],
        started_at=session["started_at"],
        ended_at=session["ended_at"],
        expected_chunks=session["expected_chunks"],
        status=session["status"],
        chunks=get_chunks(session["id"])
    )


def _require_session(session_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(limit: int = 50, offset: int = 0):
    return [_session_response(s) for s in get_sessions(limit, offset)]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session_detail(session_id: str):
    return _session_response(_require_session(session_id))


def session_segments(session_id: str) -> list[dict]:
    """Segments with start/end converted from chunk time to session time."""
    offsets = chunk_offsets(get_chunks(session_id))
    result = []
    for seg in get_segments(session_id):
        # Rows written before chunk_id existed encode it in the segment id.
        chunk_id = seg.get("chunk_id") or seg["id"].rsplit("_seg_", 1)[0]
        offset = offsets.get(chunk_id, 0)
        result.append({**seg, "start_ms": seg["start_ms"] + offset, "end_ms": seg["end_ms"] + offset})
    return sorted(result, key=lambda s: s["start_ms"])


@router.get("/sessions/{session_id}/segments", response_model=list[SegmentResponse])
def get_session_segments(session_id: str):
    _require_session(session_id)
    return [SegmentResponse(**seg) for seg in session_segments(session_id)]


# Declared before /audio/{chunk_seq} so "full" isn't parsed as a chunk number.
@router.get("/sessions/{session_id}/audio/full")
def get_full_audio(session_id: str):
    _require_session(session_id)
    try:
        audio_path = build_session_audio(session_id, get_chunks(session_id), settings.audio_dir)
    except FileNotFoundError:
        raise HTTPException(404, "No audio chunks available")
    except (OSError, RuntimeError) as e:
        raise HTTPException(500, f"Could not join session audio (is ffmpeg installed?): {e}")
    return FileResponse(audio_path, media_type="audio/mp4", filename=f"{session_id}_full.m4a")


@router.get("/sessions/{session_id}/audio/{chunk_seq:int}")
def get_chunk_audio(session_id: str, chunk_seq: int):
    _require_session(session_id)
    chunk = next((c for c in get_chunks(session_id) if c["seq"] == chunk_seq), None)
    if not chunk:
        raise HTTPException(404, "Chunk not found")

    audio_path = Path(chunk["audio_path"])
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")

    return FileResponse(audio_path, media_type="audio/mp4", filename=f"chunk_{chunk_seq}.m4a")


@router.get("/sessions/{session_id}/notes", response_model=NotesResponse)
def list_notes(session_id: str):
    _require_session(session_id)
    return NotesResponse(documents=get_notes(session_id))


@router.post("/sessions/{session_id}/notes", response_model=NotesResponse)
def generate_notes(session_id: str, request: NotesRequest):
    session = _require_session(session_id)
    segments = session_segments(session_id)
    if not segments:
        raise HTTPException(400, "No segments available for notes generation")

    try:
        documents = generate_documents(session, segments, request.model)
    except NotesError as e:
        raise HTTPException(502, str(e))

    save_notes(session_id, documents)
    return NotesResponse(documents=get_notes(session_id))
