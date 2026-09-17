import asyncio
import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from app.db.database import (
    get_chunks, get_pending_chunks, get_session, get_sessions_ready_for_analysis, update_chunk_status,
    update_chunk_duration, set_chunk_transcribed, set_session_analysis, replace_session_segments,
    refresh_session_status, reset_interrupted_work
)
from app.services.analysis import assign_speaker_roles, classify_session, parse_deadline
from app.services.audio import chunk_offsets, probe_duration_ms
from app.services.processing import add_loudness, reduce_noise, transcribe_audio
from app.core.config import settings


POLL_INTERVAL_S = 10


class MissingTranscriptsError(Exception):
    def __init__(self, chunk_ids: list[str]):
        super().__init__(f"Missing transcripts for {', '.join(chunk_ids)}")
        self.chunk_ids = chunk_ids


class ProcessingWorker:
    """Processes lectures on a background thread, in two stages:

    1. Each chunk is transcribed as soon as it arrives.
    2. Once the phone has finalized a session and every chunk is transcribed, the whole
       session is classified and its segments are written.

    Transcription and LLM calls take minutes and would otherwise block the event loop,
    making the server look offline to the phone."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self):
        reset_interrupted_work()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="processing-worker", daemon=True)
        self._thread.start()

    async def stop(self):
        self._stop.set()
        if self._thread:
            # Work mid-way can't be interrupted; it is reset and redone on next start.
            await asyncio.to_thread(self._thread.join, 5)

    def _run_loop(self):
        while not self._stop.is_set():
            did_work = False
            try:
                # Transcribe everything waiting before analysing, so a finished lecture isn't
                # held up behind another session's classification.
                for chunk in get_pending_chunks():
                    if self._stop.is_set():
                        break
                    self._transcribe_chunk(chunk)
                    did_work = True
                for session_id in get_sessions_ready_for_analysis():
                    if self._stop.is_set() or get_pending_chunks():
                        break
                    self._analyze_session(session_id)
                    did_work = True
            except Exception:
                print(f"Processing loop error:\n{traceback.format_exc()}")
            if not did_work:
                self._stop.wait(POLL_INTERVAL_S)

    def _transcribe_chunk(self, chunk: dict):
        chunk_id = chunk["id"]
        audio_path = Path(chunk["audio_path"])
        session_id = chunk["session_id"]
        denoised_path = audio_path.with_name(audio_path.stem + ".denoised.wav")

        print(f"Transcribing chunk {chunk_id}")
        update_chunk_status(chunk_id, "processing")

        try:
            duration_ms = probe_duration_ms(audio_path)
            if duration_ms:
                update_chunk_duration(chunk_id, duration_ms)

            source = denoised_path if reduce_noise(audio_path, denoised_path) else audio_path
            segments = transcribe_audio(source)
            add_loudness(source, segments)

            transcript_path = settings.transcripts_dir / f"{chunk_id}.json"
            transcript_path.write_text(json.dumps(segments), encoding="utf-8")
            set_chunk_transcribed(chunk_id, str(transcript_path))
            print(f"Chunk {chunk_id} transcribed ({len(segments)} segments)")

        except Exception as e:
            print(f"Chunk {chunk_id} transcription failed:\n{traceback.format_exc()}")
            update_chunk_status(chunk_id, "failed", f"{type(e).__name__}: {e}")

        finally:
            denoised_path.unlink(missing_ok=True)
            refresh_session_status(session_id)

    def _analyze_session(self, session_id: str):
        print(f"Analysing session {session_id}")
        set_session_analysis(session_id, "running")
        refresh_session_status(session_id)

        try:
            session = get_session(session_id)
            lecture_start = datetime.fromtimestamp(session["started_at"] / 1000)

            entries = load_session_transcript(get_chunks(session_id))
            assign_speaker_roles(entries)
            segments = classify_session(entries, lecture_start)
            for seg in segments:
                seg["inferred_deadline"] = parse_deadline(seg.pop("deadline", None), lecture_start)

            replace_session_segments(session_id, segments)
            set_session_analysis(session_id, "done")
            print(f"Session {session_id} analysed ({len(segments)} segments)")

        except MissingTranscriptsError as e:
            # Chunks from before transcripts were saved: transcribe them again, then retry.
            for chunk_id in e.chunk_ids:
                update_chunk_status(chunk_id, "pending")
            set_session_analysis(session_id, None)

        except Exception as e:
            print(f"Session {session_id} analysis failed:\n{traceback.format_exc()}")
            set_session_analysis(session_id, "failed", f"{type(e).__name__}: {e}")

        finally:
            refresh_session_status(session_id)


def load_session_transcript(chunks: list[dict]) -> list[dict]:
    """All chunks' transcript segments on one session timeline, in order."""
    offsets = chunk_offsets(chunks)
    missing = []
    entries = []
    for chunk in sorted(chunks, key=lambda c: c["seq"]):
        path = Path(chunk["transcript_path"] or settings.transcripts_dir / f"{chunk['id']}.json")
        if not path.exists():
            missing.append(chunk["id"])
            continue
        offset = offsets[chunk["id"]]
        for seg in json.loads(path.read_text(encoding="utf-8")):
            if not seg["text"]:
                continue
            entries.append({
                "start_ms": offset + int(seg["start"] * 1000),
                "end_ms": offset + int(seg["end"] * 1000),
                "text": seg["text"],
                "rms_db": seg.get("rms_db"),
                "chunk_id": chunk["id"],
            })
    if missing:
        raise MissingTranscriptsError(missing)
    return entries


processing_worker = ProcessingWorker()
