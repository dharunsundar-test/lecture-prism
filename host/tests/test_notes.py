import pytest
from app.core.config import settings
from app.services import notes as notes_module
from app.services.notes import NotesError, generate_documents, parse_note_items, split_lines


def test_note_items_pick_up_timestamps_in_common_formats():
    content = """# Stability

- [2:05] Poles in the left half-plane
* **[12:30]** Routh–Hurwitz criterion
1. [1:02:03] Nyquist plot
Plain paragraph [75:10] with a timestamp mid-line
- No timestamp
"""
    assert parse_note_items(content) == [
        {"kind": "heading", "text": "Stability", "start_ms": None},
        {"kind": "item", "text": "Poles in the left half-plane", "start_ms": 125_000},
        {"kind": "item", "text": "Routh–Hurwitz criterion", "start_ms": 750_000},
        {"kind": "item", "text": "Nyquist plot", "start_ms": 3_723_000},
        {"kind": "text", "text": "Plain paragraph  with a timestamp mid-line", "start_ms": 4_510_000},
        {"kind": "item", "text": "No timestamp", "start_ms": None},
    ]


def test_split_lines_respects_the_size_limit():
    lines = ["a" * 40, "b" * 40, "c" * 40, "d" * 200]
    parts = split_lines(lines, max_chars=100)
    assert parts == ["a" * 40 + "\n" + "b" * 40, "c" * 40, "d" * 200]
    assert split_lines([], 100) == []


def test_documents_are_generated_from_timestamped_lines_in_parts(monkeypatch):
    calls = []

    def fake_complete(system_prompt, text, model):
        calls.append((system_prompt, text))
        return f"- part {len(calls)}"

    monkeypatch.setattr(notes_module, "_complete", fake_complete)
    monkeypatch.setattr(notes_module, "MAX_INPUT_CHARS", 60)

    segments = [
        {"start_ms": 65_000, "category": "concept", "source_text": "Poles and zeros of a transfer function"},
        {"start_ms": 5_000, "category": "concept", "source_text": "Definition of a transfer function"},
        {"start_ms": 10_000, "category": "action_item", "source_text": "Read chapter 3"},
        {"start_ms": 20_000, "category": "announcement", "source_text": "Quiz on Friday"},
        {"start_ms": 30_000, "category": "filler", "source_text": "Can everyone hear me?"},
    ]
    docs = {d["type"]: d for d in generate_documents({"started_at": 1_789_632_000_000}, segments, "local")}

    assert set(docs) == {"concepts", "announcements"}
    concept_calls = [text for prompt, text in calls if prompt.startswith(notes_module.PROMPTS["concepts"])]
    assert concept_calls == ["[0:05] Definition of a transfer function", "[1:05] Poles and zeros of a transfer function"]
    assert docs["concepts"]["content"].count("- part") == 2
    announcement_text = next(text for prompt, text in calls if prompt.startswith(notes_module.PROMPTS["announcements"]))
    assert announcement_text == "[0:10] Read chapter 3\n[0:20] Quiz on Friday"
    assert all(notes_module.TIMESTAMP_INSTRUCTION in prompt for prompt, _ in calls)


def test_missing_groq_key_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    with pytest.raises(NotesError, match="GROQ_API_KEY"):
        notes_module._complete("prompt", "text", "groq")
