import uuid
from app.db.database import (
    get_chunk, get_session, get_segments, finalize_session, reset_interrupted_chunks, update_chunk_status
)
from app.services import worker as worker_module
from app.services.worker import ProcessingWorker
from tests.test_api import upload


def test_chunk_processing_stores_segments_and_completes_session(lan_client, phone_headers, monkeypatch):
    sid = f"session_worker_{uuid.uuid4().hex[:8]}"
    upload(lan_client, phone_headers, sid, 0)
    finalize_session(sid, 301_000, 1)

    monkeypatch.setattr(worker_module, "probe_duration_ms", lambda path: 299_500)
    monkeypatch.setattr(worker_module, "reduce_noise", lambda src, dst: False)
    monkeypatch.setattr(worker_module, "transcribe_audio", lambda path: [
        {"id": 0, "start": 1.0, "end": 9.0, "text": "The quiz is next Tuesday.", "words": []},
    ])
    monkeypatch.setattr(worker_module, "classify_segments", lambda segments, lecture_start: [
        {**segments[0], "category": "announcement", "summary": "Quiz", "deadline": "next Tuesday"},
    ])

    ProcessingWorker()._process_chunk(get_chunk(f"{sid}_chunk_0"))

    chunk = get_chunk(f"{sid}_chunk_0")
    assert chunk["status"] == "done"
    assert chunk["duration_ms"] == 299_500
    [segment] = get_segments(sid)
    assert segment["category"] == "announcement"
    assert segment["chunk_id"] == f"{sid}_chunk_0"
    assert segment["inferred_deadline"] is not None
    assert get_session(sid)["status"] == "done"


def test_classifier_failure_marks_chunk_failed(lan_client, phone_headers, monkeypatch):
    sid = f"session_worker_{uuid.uuid4().hex[:8]}"
    upload(lan_client, phone_headers, sid, 0)

    monkeypatch.setattr(worker_module, "probe_duration_ms", lambda path: None)
    monkeypatch.setattr(worker_module, "reduce_noise", lambda src, dst: False)
    monkeypatch.setattr(worker_module, "transcribe_audio", lambda path: [
        {"id": 0, "start": 0.0, "end": 5.0, "text": "Hello", "words": []},
    ])

    def broken_classifier(segments, lecture_start):
        raise ValueError("Classifier returned an unexpected JSON shape")

    monkeypatch.setattr(worker_module, "classify_segments", broken_classifier)

    ProcessingWorker()._process_chunk(get_chunk(f"{sid}_chunk_0"))

    chunk = get_chunk(f"{sid}_chunk_0")
    assert chunk["status"] == "failed"
    assert "unexpected JSON shape" in chunk["error_msg"]
    assert get_segments(sid) == []


def test_interrupted_chunks_are_requeued(lan_client, phone_headers):
    sid = f"session_worker_{uuid.uuid4().hex[:8]}"
    upload(lan_client, phone_headers, sid, 0)
    update_chunk_status(f"{sid}_chunk_0", "processing")
    reset_interrupted_chunks()
    assert get_chunk(f"{sid}_chunk_0")["status"] == "pending"
