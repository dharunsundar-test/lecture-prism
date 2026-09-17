import asyncio
import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from app.db.database import (
    get_pending_chunks, get_session, update_chunk_status, update_chunk_duration,
    replace_chunk_segments, refresh_session_status, reset_interrupted_chunks
)
from app.services.audio import probe_duration_ms
from app.services.processing import (
    reduce_noise, transcribe_audio, detect_speaker_roles, classify_segments, parse_deadline
)
from app.core.config import settings


POLL_INTERVAL_S = 10


class ProcessingWorker:
    """Processes chunks on a background thread. Transcription and LLM calls take minutes and
    would otherwise block the event loop, making the server look offline to the phone."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self):
        reset_interrupted_chunks()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="processing-worker", daemon=True)
        self._thread.start()

    async def stop(self):
        self._stop.set()
        if self._thread:
            # A chunk mid-transcription can't be interrupted; it is reset to pending on next start.
            await asyncio.to_thread(self._thread.join, 5)

    def _run_loop(self):
        while not self._stop.is_set():
            pending = []
            try:
                pending = get_pending_chunks()
                for chunk in pending:
                    if self._stop.is_set():
                        break
                    self._process_chunk(chunk)
            except Exception:
                print(f"Processing loop error:\n{traceback.format_exc()}")
            if not pending:
                self._stop.wait(POLL_INTERVAL_S)

    def _process_chunk(self, chunk: dict):
        chunk_id = chunk["id"]
        audio_path = Path(chunk["audio_path"])
        session_id = chunk["session_id"]
        denoised_path = audio_path.with_name(audio_path.stem + ".denoised.wav")

        print(f"Processing chunk {chunk_id}")
        update_chunk_status(chunk_id, "processing")

        try:
            session = get_session(session_id)
            lecture_start = datetime.fromtimestamp(session["started_at"] / 1000)

            duration_ms = probe_duration_ms(audio_path)
            if duration_ms:
                update_chunk_duration(chunk_id, duration_ms)

            source = denoised_path if reduce_noise(audio_path, denoised_path) else audio_path
            segments = transcribe_audio(source)
            (settings.transcripts_dir / f"{chunk_id}.json").write_text(json.dumps(segments), encoding="utf-8")

            segments = detect_speaker_roles(segments)
            classified = classify_segments(segments, lecture_start) if segments else []

            replace_chunk_segments(chunk_id, session_id, [
                {
                    "start_ms": int(seg["start"] * 1000),
                    "end_ms": int(seg["end"] * 1000),
                    "category": seg["category"],
                    "summary": seg.get("summary"),
                    "inferred_deadline": parse_deadline(seg.get("deadline"), lecture_start),
                    "source_text": seg["text"],
                }
                for seg in classified
            ])

            update_chunk_status(chunk_id, "done")
            print(f"Chunk {chunk_id} processed successfully ({len(classified)} segments)")

        except Exception as e:
            print(f"Chunk {chunk_id} processing failed:\n{traceback.format_exc()}")
            update_chunk_status(chunk_id, "failed", f"{type(e).__name__}: {e}")

        finally:
            denoised_path.unlink(missing_ok=True)
            refresh_session_status(session_id)


processing_worker = ProcessingWorker()
