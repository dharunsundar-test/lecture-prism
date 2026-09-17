from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import aiofiles
import uuid
from pathlib import Path
from app.core.config import settings
from app.db.database import (
    create_session, create_chunk, update_session_status,
    get_session, get_sessions, get_chunks, update_chunk_status,
    get_segments, create_segment
)

router = APIRouter()


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
    status: str
    chunks: list


class NotesRequest(BaseModel):
    model: str = "local"


class NotesResponse(BaseModel):
    documents: List[dict]


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.post("/api/chunks/upload", response_model=ChunkUploadResponse)
async def upload_chunk(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    seq: int = Form(...),
    course_tag: str = Form(...),
    started_at: int = Form(...),
    ended_at: int = Form(...),
):
    session = get_session(session_id)
    if not session:
        create_session(session_id, course_tag, started_at)

    chunk_id = f"{session_id}_chunk_{seq}"
    audio_dir = settings.audio_dir / session_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"chunk_{seq}.m4a"

    async with aiofiles.open(audio_path, "wb") as f:
        content = await audio.read()
        await f.write(content)

    duration_ms = ended_at - started_at
    size_bytes = len(content)

    create_chunk(chunk_id, session_id, seq, str(audio_path), duration_ms, size_bytes, started_at, ended_at)

    return ChunkUploadResponse(chunk_id=chunk_id, status="pending")


@router.get("/api/sessions", response_model=list[SessionResponse])
async def list_sessions(limit: int = 50, offset: int = 0):
    sessions = get_sessions(limit, offset)
    result = []
    for s in sessions:
        chunks = get_chunks(s["id"])
        result.append(SessionResponse(
            id=s["id"],
            course_tag=s["course_tag"],
            started_at=s["started_at"],
            ended_at=s["ended_at"],
            status=s["status"],
            chunks=chunks
        ))
    return result


@router.get("/api/sessions/{session_id}", response_model=SessionResponse)
async def get_session_detail(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    chunks = get_chunks(session_id)
    return SessionResponse(
        id=session["id"],
        course_tag=session["course_tag"],
        started_at=session["started_at"],
        ended_at=session["ended_at"],
        status=session["status"],
        chunks=chunks
    )


@router.get("/api/sessions/{session_id}/segments", response_model=list[SegmentResponse])
async def get_session_segments(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    segments = get_segments(session_id)
    return [SegmentResponse(**seg) for seg in segments]


@router.get("/api/sessions/{session_id}/audio/{chunk_seq}")
async def get_chunk_audio(session_id: str, chunk_seq: int):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    chunks = get_chunks(session_id)
    chunk = next((c for c in chunks if c["seq"] == chunk_seq), None)
    if not chunk:
        raise HTTPException(404, "Chunk not found")

    audio_path = Path(chunk["audio_path"])
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")

    return FileResponse(audio_path, media_type="audio/m4a", filename=f"chunk_{chunk_seq}.m4a")


@router.get("/api/sessions/{session_id}/audio/full")
async def get_full_audio(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    chunks = get_chunks(session_id)
    synced_chunks = [c for c in chunks if c["status"] in ("synced", "done")]
    if not synced_chunks:
        raise HTTPException(404, "No audio chunks available")

    # For simplicity, serve the first chunk as a placeholder
    # In production, you'd concatenate chunks
    first_chunk = synced_chunks[0]
    audio_path = Path(first_chunk["audio_path"])
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")

    return FileResponse(audio_path, media_type="audio/m4a", filename=f"{session_id}_full.m4a")


@router.post("/api/sessions/{session_id}/notes", response_model=NotesResponse)
async def generate_notes(session_id: str, request: NotesRequest):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    segments = get_segments(session_id)
    if not segments:
        raise HTTPException(400, "No segments available for notes generation")

    # Group segments by category
    categorized = {}
    for seg in segments:
        cat = seg["category"]
        if cat not in categorized:
            categorized[cat] = []
        categorized[cat].append(seg)

    documents = []
    for cat, segs in categorized.items():
        if cat == "filler":
            continue
        text = "\n".join(f"[{s['start_ms']}-{s['end_ms']}] {s['source_text']}" for s in segs)
        summary = await _generate_category_notes(cat, text, request.model)
        doc_type = _category_to_doc_type(cat)
        documents.append({
            "id": f"{session_id}_{doc_type}",
            "session_id": session_id,
            "type": doc_type,
            "content": summary,
            "generated_at": int(__import__('time').time() * 1000),
            "model": request.model
        })

    return NotesResponse(documents=documents)


async def _generate_category_notes(category: str, text: str, model: str) -> str:
    try:
        import requests
        import os

        if model == "groq":
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                return "Groq API key not configured"
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama3-70b-8192",
                    "messages": [
                        {"role": "system", "content": _get_system_prompt(category)},
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                },
                timeout=60
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
        else:
            ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
            model_name = os.getenv("NOTES_MODEL", "llama3.1:8b")
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={"model": model_name, "prompt": _get_system_prompt(category) + "\n\n" + text, "stream": False},
                timeout=120
            )
            if response.status_code == 200:
                return response.json()["response"]
    except Exception as e:
        print(f"Notes generation failed: {e}")

    return f"Failed to generate notes for {category}"


def _get_system_prompt(category: str) -> str:
    prompts = {
        "concept": "Summarize the key concepts and definitions from this lecture transcript. Use clear headings and bullet points.",
        "example": "Extract and document all worked examples, problems solved, and demonstrations from this transcript.",
        "announcement": "List all announcements, dates, deadlines, exam info, quiz dates, and administrative details mentioned.",
        "qa": "Document all questions asked and answers given during Q&A sections.",
        "action_item": "List all action items, assignments, and tasks mentioned with any deadlines.",
    }
    return prompts.get(category, "Summarize this transcript section.")


def _category_to_doc_type(category: str) -> str:
    mapping = {
        "concept": "concepts",
        "example": "examples",
        "announcement": "announcements",
        "qa": "qa",
        "action_item": "announcements",
    }
    return mapping.get(category, "notes")