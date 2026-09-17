import asyncio
import uuid
from pathlib import Path
from app.db.database import (
    get_chunks, get_pending_chunks, update_chunk_status, create_segment, update_session_status
)
from app.services.processing import (
    reduce_noise, transcribe_audio, detect_speaker_roles, classify_segments
)
from app.core.config import settings


class ProcessingWorker:
    def __init__(self):
        self.running = False
        self.task: asyncio.Task | None = None

    async def start(self):
        self.running = True
        self.task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        while self.running:
            try:
                await self._process_pending()
            except Exception as e:
                print(f"Processing loop error: {e}")
            await asyncio.sleep(10)

    async def _process_pending(self):
        pending = get_pending_chunks()
        for chunk in pending:
            if not self.running:
                break
            await self._process_chunk(chunk)

    async def _process_chunk(self, chunk: dict):
        chunk_id = chunk["id"]
        audio_path = Path(chunk["audio_path"])
        session_id = chunk["session_id"]

        print(f"Processing chunk {chunk_id}")

        update_chunk_status(chunk_id, "processing")

        try:
            denoised_path = audio_path.with_suffix(".wav")
            if not reduce_noise(audio_path, denoised_path):
                denoised_path = audio_path

            segments = transcribe_audio(denoised_path)
            if not segments:
                raise Exception("Transcription returned no segments")

            segments = detect_speaker_roles(segments)
            classified = classify_segments(segments)

            for i, seg in enumerate(classified):
                segment_id = f"{chunk_id}_seg_{i}"
                deadline = None
                if seg.get("deadline"):
                    try:
                        from dateparser import parse as parse_date
                        dt = parse_date(seg["deadline"])
                        if dt:
                            deadline = int(dt.timestamp() * 1000)
                    except Exception:
                        pass

                create_segment(
                    segment_id=segment_id,
                    session_id=session_id,
                    start_ms=int(seg["start"] * 1000),
                    end_ms=int(seg["end"] * 1000),
                    category=seg["category"],
                    summary=seg.get("summary"),
                    inferred_deadline=deadline,
                    source_text=seg["text"]
                )

            update_chunk_status(chunk_id, "done")
            print(f"Chunk {chunk_id} processed successfully")

        except Exception as e:
            print(f"Chunk {chunk_id} processing failed: {e}")
            update_chunk_status(chunk_id, "failed", str(e))

        finally:
            if denoised_path != audio_path and denoised_path.exists():
                denoised_path.unlink(missing_ok=True)

            chunks = get_chunks(session_id)
            if all(c["status"] in ("done", "failed") for c in chunks):
                if all(c["status"] == "done" for c in chunks):
                    update_session_status(session_id, "done")
                else:
                    update_session_status(session_id, "failed")


processing_worker = ProcessingWorker()