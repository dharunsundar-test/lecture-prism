import json
import uuid
from app.core.config import settings
from app.db.database import (
    get_chunk, get_session, get_segments, get_sessions_ready_for_analysis, finalize_session,
    reset_interrupted_work, set_session_analysis, update_chunk_status, set_chunk_transcribed
)
from app.services import analysis as analysis_module
from app.services import worker as worker_module
from app.services.worker import ProcessingWorker
from tests.test_api import upload


def new_session_id():
    return f"session_worker_{uuid.uuid4().hex[:8]}"


def write_transcript(chunk_id, segments):
    path = settings.transcripts_dir / f"{chunk_id}.json"
    path.write_text(json.dumps(segments), encoding="utf-8")
    set_chunk_transcribed(chunk_id, str(path))


def test_chunk_transcription_saves_transcript_with_loudness(lan_client, phone_headers, monkeypatch):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)

    monkeypatch.setattr(worker_module, "probe_duration_ms", lambda path: 299_500)
    monkeypatch.setattr(worker_module, "reduce_noise", lambda src, dst: False)
    monkeypatch.setattr(worker_module, "transcribe_audio", lambda path: [
        {"id": 0, "start": 1.0, "end": 9.0, "text": "Today: stability.", "words": []},
    ])

    def fake_loudness(path, segments):
        for seg in segments:
            seg["rms_db"] = -21.5

    monkeypatch.setattr(worker_module, "add_loudness", fake_loudness)

    ProcessingWorker()._transcribe_chunk(get_chunk(f"{sid}_chunk_0"))

    chunk = get_chunk(f"{sid}_chunk_0")
    assert chunk["status"] == "transcribed"
    assert chunk["duration_ms"] == 299_500
    saved = json.loads(open(chunk["transcript_path"], encoding="utf-8").read())
    assert saved[0]["rms_db"] == -21.5


def test_session_is_analysed_once_finalized_and_fully_transcribed(lan_client, phone_headers, monkeypatch):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    upload(lan_client, phone_headers, sid, 1)
    write_transcript(f"{sid}_chunk_0", [
        {"start": 290.0, "end": 299.0, "text": "The quiz is next Tuesday.", "rms_db": -20.0},
    ])
    assert sid not in get_sessions_ready_for_analysis()  # chunk 1 still pending

    write_transcript(f"{sid}_chunk_1", [
        {"start": 1.0, "end": 5.0, "text": "It covers chapter three.", "rms_db": -20.0},
        {"start": 10.0, "end": 20.0, "text": "A transfer function is...", "rms_db": -20.0},
    ])
    assert sid not in get_sessions_ready_for_analysis()  # not finalized yet
    finalize_session(sid, 601_000, 2)
    assert sid in get_sessions_ready_for_analysis()

    seen_windows = []

    def fake_classify_window(context, core, lecture_start):
        seen_windows.append([e["text"] for e in core])
        return [
            {"start": 0, "end": 1, "category": "announcement", "summary": "Quiz on chapter 3", "deadline": "next Tuesday"},
            {"start": 2, "end": 2, "category": "concept", "summary": "Transfer functions", "deadline": None},
        ]

    monkeypatch.setattr(analysis_module, "classify_window", fake_classify_window)

    ProcessingWorker()._analyze_session(sid)

    # One window spans the chunk boundary, so the announcement isn't split in two.
    assert seen_windows == [["The quiz is next Tuesday.", "It covers chapter three.", "A transfer function is..."]]
    announcement, concept = get_segments(sid)
    # Chunk 0 is 300 s long (upload's wall-clock duration), so chunk 1 starts at 300 s.
    assert (announcement["start_ms"], announcement["end_ms"]) == (290_000, 305_000)
    assert announcement["category"] == "announcement"
    assert announcement["inferred_deadline"] is not None
    assert announcement["source_text"] == "The quiz is next Tuesday. It covers chapter three."
    assert (concept["start_ms"], concept["category"]) == (310_000, "concept")
    session = get_session(sid)
    assert (session["analysis_status"], session["status"]) == ("done", "done")


def test_analysis_failure_is_recorded_on_the_session(lan_client, phone_headers, monkeypatch):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    write_transcript(f"{sid}_chunk_0", [{"start": 0.0, "end": 5.0, "text": "Hello", "rms_db": None}])
    finalize_session(sid, 301_000, 1)

    def broken(context, core, lecture_start):
        raise ValueError("Classifier returned an unexpected JSON shape")

    monkeypatch.setattr(analysis_module, "classify_window", broken)
    ProcessingWorker()._analyze_session(sid)

    session = get_session(sid)
    assert session["status"] == "failed"
    assert session["analysis_status"] == "failed"
    assert "unexpected JSON shape" in session["error_msg"]
    assert sid not in get_sessions_ready_for_analysis()  # not retried in a loop


def test_chunks_without_saved_transcripts_are_transcribed_again(lan_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    update_chunk_status(f"{sid}_chunk_0", "done")  # finished by the per-chunk pipeline, no transcript file
    finalize_session(sid, 301_000, 1)
    assert sid in get_sessions_ready_for_analysis()

    ProcessingWorker()._analyze_session(sid)

    assert get_chunk(f"{sid}_chunk_0")["status"] == "pending"
    assert get_session(sid)["analysis_status"] is None


def test_silent_session_completes_with_no_segments(lan_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    write_transcript(f"{sid}_chunk_0", [])
    finalize_session(sid, 301_000, 1)

    ProcessingWorker()._analyze_session(sid)

    assert get_segments(sid) == []
    assert get_session(sid)["status"] == "done"


def test_interrupted_work_is_requeued(lan_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    update_chunk_status(f"{sid}_chunk_0", "processing")
    set_session_analysis(sid, "running")
    reset_interrupted_work()
    assert get_chunk(f"{sid}_chunk_0")["status"] == "pending"
    assert get_session(sid)["analysis_status"] is None
