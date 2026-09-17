import hashlib
import uuid
from datetime import datetime
from app.core.config import Settings
from app.db.database import get_chunks, replace_chunk_segments, update_chunk_status
from app.services.processing import _extract_items, parse_deadline


def upload(client, headers, session_id, seq, body=b"fake-aac-bytes", **extra):
    data = {
        "session_id": session_id, "seq": str(seq), "course_tag": "cs",
        "started_at": str(1_000 + seq * 300_000), "ended_at": str(301_000 + seq * 300_000),
        "session_started_at": "1000", **extra,
    }
    return client.post("/api/chunks/upload", headers=headers,
                       files={"audio": (f"chunk_{seq}.m4a", body, "audio/mp4")}, data=data)


def new_session_id():
    return f"session_test_{uuid.uuid4().hex[:8]}"


def test_retried_upload_is_accepted_without_duplicating(lan_client, phone_headers):
    sid = new_session_id()
    assert upload(lan_client, phone_headers, sid, 0).status_code == 200
    retry = upload(lan_client, phone_headers, sid, 0)
    assert retry.status_code == 200
    assert len(get_chunks(sid)) == 1


def test_reupload_with_new_content_replaces_chunk(lan_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0, body=b"first")
    update_chunk_status(f"{sid}_chunk_0", "done")
    assert upload(lan_client, phone_headers, sid, 0, body=b"second").json()["status"] == "pending"
    chunk = get_chunks(sid)[0]
    assert chunk["sha256"] == hashlib.sha256(b"second").hexdigest()
    assert chunk["status"] == "pending"


def test_checksum_mismatch_is_rejected(lan_client, phone_headers):
    response = upload(lan_client, phone_headers, new_session_id(), 0, sha256="0" * 64)
    assert response.status_code == 400


def test_lan_requests_need_the_pair_token(lan_client, local_client, phone_headers):
    assert lan_client.get("/api/ping").status_code == 401
    assert lan_client.get("/api/ping", headers={"X-Pair-Token": "wrong"}).status_code == 401
    assert lan_client.get("/api/ping", headers=phone_headers).status_code == 200
    assert local_client.get("/api/sessions").status_code == 200
    assert lan_client.get("/health").status_code == 200


def test_invalid_session_id_is_rejected(lan_client, phone_headers):
    assert upload(lan_client, phone_headers, "..%2F..%2Fescape", 0).status_code == 400


def test_full_audio_route_is_not_shadowed_and_supports_seeking(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0, body=b"0123456789")
    full = local_client.get(f"/api/sessions/{sid}/audio/full")
    assert full.status_code == 200
    assert full.content == b"0123456789"
    ranged = local_client.get(f"/api/sessions/{sid}/audio/full", headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert local_client.get(f"/api/sessions/{sid}/audio/0").status_code == 200


def test_session_is_done_only_after_finalize_and_all_chunks(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    update_chunk_status(f"{sid}_chunk_0", "done")

    finalize = lan_client.post(f"/api/sessions/{sid}/finalize", headers=phone_headers,
                               json={"ended_at": 601_000, "chunk_count": 2, "course_tag": "cs", "started_at": 1000})
    assert finalize.status_code == 200
    assert finalize.json()["status"] == "capturing"  # chunk 1 hasn't arrived yet
    assert finalize.json()["ended_at"] == 601_000

    upload(lan_client, phone_headers, sid, 1)
    assert local_client.get(f"/api/sessions/{sid}").json()["status"] == "processing"

    update_chunk_status(f"{sid}_chunk_1", "done")
    from app.db.database import refresh_session_status
    refresh_session_status(sid)
    assert local_client.get(f"/api/sessions/{sid}").json()["status"] == "done"


def test_segment_times_are_session_relative(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    upload(lan_client, phone_headers, sid, 1)
    replace_chunk_segments(f"{sid}_chunk_1", sid, [
        {"start_ms": 1_000, "end_ms": 4_000, "category": "concept", "source_text": "Transfer functions"},
    ])
    replace_chunk_segments(f"{sid}_chunk_1", sid, [  # reprocessing must not duplicate rows
        {"start_ms": 1_000, "end_ms": 4_000, "category": "concept", "source_text": "Transfer functions"},
    ])
    segments = local_client.get(f"/api/sessions/{sid}/segments").json()
    assert len(segments) == 1
    assert segments[0]["start_ms"] == 301_000  # chunk 0 is 300 s long
    assert segments[0]["end_ms"] == 304_000


def test_pairing_page_is_local_only(lan_client, local_client, phone_headers):
    assert lan_client.get("/api/pair").status_code == 403
    page = local_client.get("/api/pair")
    assert page.status_code == 200
    assert "<svg" in page.text
    assert phone_headers["X-Pair-Token"] in page.text
    assert local_client.get("/api/pair/info").json()["token"] == phone_headers["X-Pair-Token"]


def test_env_file_keys_from_readme_do_not_crash_startup(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HOST=0.0.0.0\nOLLAMA_URL=http://ollama:11434\nGROQ_API_KEY=abc\nSOMETHING_ELSE=1\n")
    loaded = Settings(_env_file=env)
    assert loaded.ollama_url == "http://ollama:11434"
    assert loaded.groq_api_key == "abc"


def test_classifier_output_shapes():
    assert _extract_items({"segments": [{"index": 0}]}) == [{"index": 0}]
    assert _extract_items({"index": 0, "category": "qa"}) == [{"index": 0, "category": "qa"}]
    assert _extract_items({"results": [{"index": 1}]}) == [{"index": 1}]


def test_relative_deadlines_resolve_against_lecture_date():
    lecture = datetime(2026, 9, 17, 10, 0)  # a Thursday
    deadline = datetime.fromtimestamp(parse_deadline("next Tuesday", lecture) / 1000)
    assert deadline.date() == datetime(2026, 9, 22).date()
    friday = datetime.fromtimestamp(parse_deadline("quiz this Friday", lecture) / 1000)
    assert friday.date() == datetime(2026, 9, 18).date()
    iso = datetime.fromtimestamp(parse_deadline("2026-10-05", lecture) / 1000)
    assert iso.date() == datetime(2026, 10, 5).date()
    assert parse_deadline(None, lecture) is None
    assert parse_deadline("Chapter 5 review", lecture) is None
