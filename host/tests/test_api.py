import hashlib
import uuid
from app.core.config import Settings
from app.db.database import (
    db_cursor, get_chunks, refresh_session_status, replace_session_segments, save_notes,
    set_session_analysis, update_chunk_status
)


def upload(client, headers, session_id, seq, body=b"fake-aac-bytes", **extra):
    data = {
        "session_id": session_id, "seq": str(seq), "course_tag": "cs",
        "started_at": str(1_000 + seq * 300_000), "ended_at": str(301_000 + seq * 300_000),
        "session_started_at": "1000", **extra,
    }
    return client.post("/api/chunks/upload", headers=headers,
                       files={"audio": (f"chunk_{seq}.m4a", body, "audio/mp4")}, data=data)


def finalize(client, headers, session_id, chunk_count):
    return client.post(f"/api/sessions/{session_id}/finalize", headers=headers,
                       json={"ended_at": 1_000 + chunk_count * 300_000, "chunk_count": chunk_count,
                             "course_tag": "cs", "started_at": 1000})


def new_session_id():
    return f"session_test_{uuid.uuid4().hex[:8]}"


def test_retried_upload_is_accepted_without_duplicating(lan_client, phone_headers):
    sid = new_session_id()
    assert upload(lan_client, phone_headers, sid, 0).status_code == 200
    retry = upload(lan_client, phone_headers, sid, 0)
    assert retry.status_code == 200
    assert len(get_chunks(sid)) == 1


def test_reupload_with_new_content_replaces_chunk_and_invalidates_analysis(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0, body=b"first")
    update_chunk_status(f"{sid}_chunk_0", "transcribed")
    set_session_analysis(sid, "done")

    assert upload(lan_client, phone_headers, sid, 0, body=b"second").json()["status"] == "pending"
    chunk = get_chunks(sid)[0]
    assert chunk["sha256"] == hashlib.sha256(b"second").hexdigest()
    assert chunk["status"] == "pending"
    assert local_client.get(f"/api/sessions/{sid}").json()["analysis_status"] is None


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


def test_session_is_done_only_after_all_chunks_and_analysis(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    update_chunk_status(f"{sid}_chunk_0", "transcribed")

    response = finalize(lan_client, phone_headers, sid, 2)
    assert response.status_code == 200
    assert response.json()["status"] == "capturing"  # chunk 1 hasn't arrived yet
    assert response.json()["ended_at"] == 601_000

    upload(lan_client, phone_headers, sid, 1)
    assert local_client.get(f"/api/sessions/{sid}").json()["status"] == "processing"

    update_chunk_status(f"{sid}_chunk_1", "transcribed")
    refresh_session_status(sid)
    assert local_client.get(f"/api/sessions/{sid}").json()["status"] == "processing"  # not analysed yet

    set_session_analysis(sid, "done")
    refresh_session_status(sid)
    assert local_client.get(f"/api/sessions/{sid}").json()["status"] == "done"


def test_segments_are_on_the_session_timeline(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    upload(lan_client, phone_headers, sid, 1)
    replace_session_segments(sid, [
        {"start_ms": 301_000, "end_ms": 304_000, "category": "concept", "source_text": "Transfer functions",
         "speaker_role": "lecturer"},
    ])
    # A row left by the per-chunk pipeline: chunk-relative time, converted using chunk 0's duration.
    with db_cursor() as conn:
        conn.execute(
            """INSERT INTO classified_segments (id, session_id, chunk_id, start_ms, end_ms, category, source_text)
               VALUES (?, ?, ?, 5000, 6000, 'qa', 'Legacy question')""",
            (f"{sid}_chunk_1_seg_0", sid, f"{sid}_chunk_1")
        )

    segments = local_client.get(f"/api/sessions/{sid}/segments").json()
    assert [(s["start_ms"], s["source_text"]) for s in segments] == [
        (301_000, "Transfer functions"),
        (305_000, "Legacy question"),
    ]
    assert segments[0]["speaker_role"] == "lecturer"

    replace_session_segments(sid, [])  # re-analysis replaces everything, legacy rows included
    assert local_client.get(f"/api/sessions/{sid}/segments").json() == []


def test_reprocess_retries_failed_work_and_finalizes_orphaned_sessions(lan_client, local_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    upload(lan_client, phone_headers, sid, 1)
    update_chunk_status(f"{sid}_chunk_0", "transcribed")
    update_chunk_status(f"{sid}_chunk_1", "failed", "boom")
    set_session_analysis(sid, "failed", "Ollama unreachable")
    refresh_session_status(sid)
    assert local_client.get(f"/api/sessions/{sid}").json()["status"] == "failed"

    # The phone never sent finalize for this session.
    session = local_client.post(f"/api/sessions/{sid}/reprocess").json()
    assert session["expected_chunks"] == 2
    assert session["analysis_status"] is None
    assert session["error_msg"] is None
    assert [c["status"] for c in session["chunks"]] == ["transcribed", "pending"]
    assert session["status"] == "processing"


def test_notes_are_returned_with_timestamped_items(local_client, lan_client, phone_headers):
    sid = new_session_id()
    upload(lan_client, phone_headers, sid, 0)
    save_notes(sid, [{
        "type": "concepts", "model": "local", "generated_at": 1,
        "content": "## Stability\n- [2:05] Poles in the left half-plane\n- No timestamp here",
    }])
    [doc] = local_client.get(f"/api/sessions/{sid}/notes").json()["documents"]
    assert doc["items"] == [
        {"kind": "heading", "text": "Stability", "start_ms": None},
        {"kind": "item", "text": "Poles in the left half-plane", "start_ms": 125_000},
        {"kind": "item", "text": "No timestamp here", "start_ms": None},
    ]


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
