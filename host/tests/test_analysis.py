from datetime import datetime
import pytest
from app.services.analysis import (
    assign_speaker_roles, build_windows, extract_items, normalize_spans, parse_deadline, spans_to_segments
)


def entry(start_s, end_s, text="...", rms_db=None, speaker=None):
    e = {"start_ms": int(start_s * 1000), "end_ms": int(end_s * 1000), "text": text, "rms_db": rms_db}
    if speaker:
        e["speaker"] = speaker
    return e


# --- windows ---------------------------------------------------------------

def test_windows_split_by_duration_with_context_from_previous_window():
    entries = [entry(t, t + 20) for t in range(0, 400, 30)]  # 0, 30, ... 390 s
    windows = build_windows(entries, window_ms=180_000, context_ms=30_000)

    cores = [[e["start_ms"] // 1000 for e in core] for _, core in windows]
    assert cores == [
        [0, 30, 60, 90, 120, 150],
        [180, 210, 240, 270, 300, 330],
        [360, 390],
    ]
    assert windows[0][0] == []
    # Context = previous-window entries ending within 30 s of this window's start.
    assert [e["start_ms"] // 1000 for e in windows[1][0]] == [150]
    assert build_windows([]) == []


# --- spans -----------------------------------------------------------------

def test_spans_cover_every_line_in_order():
    runs = normalize_spans(6, [
        {"start": 0, "end": 1, "category": "concept", "summary": "A"},
        # line 2 missing: joins the span before it
        {"start": 3, "end": 5, "category": "qa", "summary": "B"},
    ])
    assert [(s, e, span["category"]) for s, e, span in runs] == [(0, 2, "concept"), (3, 5, "qa")]


def test_spans_are_clamped_and_invalid_input_is_ignored():
    runs = normalize_spans(4, [
        {"start": "1", "end": 99, "category": "made_up", "summary": "clamped"},  # unknown category -> filler
        {"start": "x", "end": 2, "category": "qa"},
        {"start": 10, "end": 12, "category": "qa"},  # entirely out of range
        "not a dict",
    ])
    # Leading line 0 takes the first span that exists.
    assert [(s, e, span["category"]) for s, e, span in runs] == [(0, 3, "filler")]


def test_later_span_wins_overlaps_and_per_line_index_format_is_accepted():
    runs = normalize_spans(3, [
        {"start": 0, "end": 2, "category": "concept"},
        {"index": 1, "category": "example"},
    ])
    assert [(s, e, span["category"]) for s, e, span in runs] == [(0, 0, "concept"), (1, 1, "example"), (2, 2, "concept")]


def test_no_usable_spans_is_an_error():
    with pytest.raises(ValueError):
        normalize_spans(3, [{"category": "concept"}])
    assert normalize_spans(0, []) == []


def test_extract_items_accepts_common_wrappings():
    assert extract_items({"spans": [{"start": 0}]}) == [{"start": 0}]
    assert extract_items({"segments": [{"index": 0}]}) == [{"index": 0}]
    assert extract_items({"start": 0, "end": 2}) == [{"start": 0, "end": 2}]
    assert extract_items({"result": [{"start": 1}]}) == [{"start": 1}]
    assert extract_items([{"start": 0}]) == [{"start": 0}]
    with pytest.raises(ValueError):
        extract_items({"a": [1], "b": [2]})


def test_segments_take_times_text_and_majority_speaker_from_their_lines():
    core = [
        entry(0, 4, "Any questions?", speaker="lecturer"),
        entry(5, 7, "Is H(s) stable?", speaker="other"),
        entry(8, 20, "Yes, because all poles...", speaker="lecturer"),
    ]
    [segment] = spans_to_segments(core, [(0, 2, {"category": "qa", "summary": "Stability Q", "deadline": None})])
    assert segment["start_ms"] == 0 and segment["end_ms"] == 20_000
    assert segment["source_text"] == "Any questions? Is H(s) stable? Yes, because all poles..."
    assert segment["speaker_role"] == "lecturer"


# --- speaker roles ---------------------------------------------------------

def test_lecturer_is_the_level_with_most_speaking_time_even_when_quieter():
    # Phone sits among students: questions are loud and short, the lecturer is quieter but talks most.
    entries = [entry(i * 20, i * 20 + 15, rms_db=-32 + (i % 3) * 0.5) for i in range(10)]
    entries += [entry(300 + i * 10, 300 + i * 10 + 3, rms_db=-15 + (i % 2) * 0.5) for i in range(4)]
    assign_speaker_roles(entries)
    assert {e["speaker"] for e in entries[:10]} == {"lecturer"}
    assert {e["speaker"] for e in entries[10:]} == {"other"}


def test_long_stretches_count_as_lecturer_regardless_of_level():
    entries = [entry(i * 20, i * 20 + 15, rms_db=-20) for i in range(10)]
    entries += [entry(300 + i * 5, 300 + i * 5 + 3, rms_db=-35) for i in range(3)]
    entries.append(entry(400, 430, rms_db=-35))  # 30 s at the quiet level
    assign_speaker_roles(entries)
    assert [e["speaker"] for e in entries[10:13]] == ["other"] * 3
    assert entries[-1]["speaker"] == "lecturer"


def test_single_loudness_level_falls_back_to_timing():
    entries = [entry(i * 20, i * 20 + 15, rms_db=-20 + (i % 2)) for i in range(8)]
    entries.append(entry(155.5, 157, rms_db=-20))  # short reply straight after the last speech (ends 155 s)
    assign_speaker_roles(entries)
    assert entries[0]["speaker"] == "lecturer"
    assert entries[-1]["speaker"] == "other"


def test_missing_loudness_falls_back_to_timing():
    entries = [entry(0, 10), entry(10.5, 12), entry(20, 40)]
    assign_speaker_roles(entries)
    assert [e["speaker"] for e in entries] == ["lecturer", "other", "lecturer"]


# --- deadlines -------------------------------------------------------------

def test_relative_deadlines_resolve_against_lecture_date():
    lecture = datetime(2026, 9, 17, 10, 0)  # a Thursday

    def day(text):
        return datetime.fromtimestamp(parse_deadline(text, lecture) / 1000).date()

    assert day("next Tuesday") == datetime(2026, 9, 22).date()
    assert day("quiz this Friday") == datetime(2026, 9, 18).date()
    assert day("2026-10-05") == datetime(2026, 10, 5).date()
    assert parse_deadline(None, lecture) is None
    assert parse_deadline("Chapter 5 review", lecture) is None
