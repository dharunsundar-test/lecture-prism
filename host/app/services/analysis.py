"""Session-level analysis: runs once every chunk of a lecture is transcribed.

Classifying the whole session (rather than each 5-minute chunk on its own) lets topics and
announcements that straddle a chunk boundary stay together, and gives the model a little of
what came just before each window.
"""
import json
import re
from datetime import datetime
import requests
from app.core.config import settings


CATEGORIES = {"concept", "example", "announcement", "qa", "action_item", "filler"}

WINDOW_MS = 180_000
CONTEXT_MS = 30_000

# Speaker heuristic tuning. Loudness levels closer than this are treated as one voice level.
MIN_LEVEL_SEPARATION_DB = 6.0
MIN_SEGMENTS_FOR_LOUDNESS = 8
# Long stretches are the lecturer even if they're quieter (e.g. walking away from the phone).
LECTURER_MIN_DURATION_MS = 20_000


# ---------------------------------------------------------------------------
# Speaker roles
# ---------------------------------------------------------------------------

def assign_speaker_roles(entries: list[dict]) -> None:
    """Label each transcript entry 'lecturer' or 'other' in place.

    The phone may sit next to the lecturer or among the students, so the lecturer isn't
    necessarily the loudest voice. What is reliable is that the lecturer does most of the
    talking: split entries into two loudness levels and treat the level with more total
    speaking time as the lecturer. If there's no clear second level, fall back to timing.
    """
    split = _two_level_split(entries)
    if split is None:
        _assign_roles_by_timing(entries)
        return

    threshold, lecturer_is_louder = split
    for entry in entries:
        rms = entry.get("rms_db")
        duration = entry["end_ms"] - entry["start_ms"]
        if rms is None or duration >= LECTURER_MIN_DURATION_MS:
            entry["speaker"] = "lecturer"
        else:
            entry["speaker"] = "lecturer" if (rms >= threshold) == lecturer_is_louder else "other"


def _two_level_split(entries: list[dict]) -> tuple[float, bool] | None:
    points = [
        (e["rms_db"], max(e["end_ms"] - e["start_ms"], 1))
        for e in entries
        if e.get("rms_db") is not None
    ]
    if len(points) < MIN_SEGMENTS_FOR_LOUDNESS:
        return None

    # Duration-weighted 1-D k-means with two centroids.
    low = min(p[0] for p in points)
    high = max(p[0] for p in points)
    for _ in range(25):
        threshold = (low + high) / 2
        quiet = [p for p in points if p[0] < threshold]
        loud = [p for p in points if p[0] >= threshold]
        if not quiet or not loud:
            return None
        new_low = sum(v * w for v, w in quiet) / sum(w for _, w in quiet)
        new_high = sum(v * w for v, w in loud) / sum(w for _, w in loud)
        if abs(new_low - low) < 0.01 and abs(new_high - high) < 0.01:
            break
        low, high = new_low, new_high

    if high - low < MIN_LEVEL_SEPARATION_DB:
        return None
    threshold = (low + high) / 2
    loud_time = sum(w for v, w in points if v >= threshold)
    quiet_time = sum(w for v, w in points if v < threshold)
    return threshold, loud_time >= quiet_time


def _assign_roles_by_timing(entries: list[dict]) -> None:
    """Fallback: long continuous speech = lecturer; short replies straight after speech = other."""
    current = "lecturer"
    last_end = 0
    for entry in entries:
        duration = entry["end_ms"] - entry["start_ms"]
        gap = entry["start_ms"] - last_end
        if gap > 2000:
            current = "lecturer"
        elif duration < 3000 and gap < 1000:
            current = "other"
        entry["speaker"] = current
        last_end = entry["end_ms"]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def build_windows(entries: list[dict], window_ms: int = WINDOW_MS, context_ms: int = CONTEXT_MS):
    """Split the session transcript into consecutive windows of roughly window_ms.

    Returns (context, core) pairs: core entries are classified; context entries are the tail
    of the previous window, shown to the model but not classified again."""
    windows = []
    core: list[dict] = []
    for entry in entries:
        if core and entry["start_ms"] - core[0]["start_ms"] >= window_ms:
            windows.append(core)
            core = []
        core.append(entry)
    if core:
        windows.append(core)

    result = []
    for i, window in enumerate(windows):
        previous = windows[i - 1] if i else []
        context = [e for e in previous if e["end_ms"] >= window[0]["start_ms"] - context_ms]
        result.append((context, window))
    return result


def classify_session(entries: list[dict], lecture_start: datetime) -> list[dict]:
    """Classify the whole transcript window by window. Returns segments with session-relative
    times, in order. Raises if the model's answer for any window can't be used."""
    segments = []
    for context, core in build_windows(entries):
        items = classify_window(context, core, lecture_start)
        segments.extend(spans_to_segments(core, normalize_spans(len(core), items)))
    return segments


def classify_window(context: list[dict], core: list[dict], lecture_start: datetime) -> list[dict]:
    response = requests.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.classification_model,
            "prompt": build_window_prompt(context, core, lecture_start),
            "format": "json",
            "stream": False,
        },
        timeout=600,
    )
    response.raise_for_status()
    return extract_items(json.loads(response.json()["response"]))


def build_window_prompt(context: list[dict], core: list[dict], lecture_start: datetime) -> str:
    context_text = "\n".join(f"{e.get('speaker', 'lecturer').upper()}: {e['text']}" for e in context)
    lines = "\n".join(f"[{i}] {e.get('speaker', 'lecturer').upper()}: {e['text']}" for i, e in enumerate(core))
    return f"""This lecture was recorded on {lecture_start:%A %Y-%m-%d}.
Split the numbered transcript lines into consecutive spans that each cover one idea, and give each span ONE category:
- concept: definitions and explanations
- example: worked problems and demonstrations
- announcement: quiz, exam, seminar and other dates or administrative news
- qa: a question and its answer
- action_item: assignments, readings and other tasks
- filler: greetings, silence, off-topic talk

Each span covers lines "start" to "end" inclusive. Spans must be in order, must not overlap, and together must cover every numbered line.
"summary" is one sentence. For announcements and action items that mention a date, set "deadline" to that date as YYYY-MM-DD, resolving relative dates such as "next Tuesday" against the recording date; otherwise null.

Return a JSON object:
{{"spans": [{{"start": 0, "end": 3, "category": "concept", "summary": "...", "deadline": null}}]}}

Said just before (context only, do not include it in spans):
{context_text or "(start of lecture)"}

Lines:
{lines}"""


def extract_items(result) -> list[dict]:
    # Ollama's JSON mode always returns an object, but models vary in how they wrap the list.
    if isinstance(result, dict):
        for key in ("spans", "segments"):
            if isinstance(result.get(key), list):
                return result[key]
        if "start" in result or "index" in result:
            return [result]
        lists = [v for v in result.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    elif isinstance(result, list):
        return result
    raise ValueError(f"Classifier returned an unexpected JSON shape: {str(result)[:200]}")


def normalize_spans(count: int, items: list[dict]) -> list[tuple[int, int, dict]]:
    """Turn the model's spans into ordered, non-overlapping (start, end, span) runs that cover
    every line. Uncovered lines join the span before them; later spans win overlaps."""
    if count == 0:
        return []

    spans = []
    labels: list[int | None] = [None] * count
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start", item.get("index")))
            end = int(item.get("end", start))
        except (TypeError, ValueError):
            continue
        start, end = sorted((start, end))
        if start >= count or end < 0:
            continue
        start, end = max(start, 0), min(end, count - 1)

        category = item.get("category") if item.get("category") in CATEGORIES else "filler"
        spans.append({"category": category, "summary": item.get("summary"), "deadline": item.get("deadline")})
        for i in range(start, end + 1):
            labels[i] = len(spans) - 1

    if not spans:
        raise ValueError("Classifier returned no usable spans")

    first = next(label for label in labels if label is not None)
    previous = first
    for i, label in enumerate(labels):
        if label is None:
            labels[i] = previous
        else:
            previous = label

    runs = []
    run_start = 0
    for i in range(1, count + 1):
        if i == count or labels[i] != labels[run_start]:
            runs.append((run_start, i - 1, spans[labels[run_start]]))
            run_start = i
    return runs


def spans_to_segments(core: list[dict], runs: list[tuple[int, int, dict]]) -> list[dict]:
    segments = []
    for start, end, span in runs:
        lines = core[start:end + 1]
        speaking = {}
        for e in lines:
            role = e.get("speaker", "lecturer")
            speaking[role] = speaking.get(role, 0) + e["end_ms"] - e["start_ms"]
        segments.append({
            "start_ms": lines[0]["start_ms"],
            "end_ms": lines[-1]["end_ms"],
            "category": span["category"],
            "summary": span["summary"],
            "deadline": span["deadline"],
            "speaker_role": max(speaking, key=speaking.get),
            "source_text": " ".join(e["text"] for e in lines),
        })
    return segments


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

WEEKDAY_QUALIFIER_RE = re.compile(
    r"\b(?:next|this|coming|upcoming)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)


def parse_deadline(text: str | None, lecture_start: datetime) -> int | None:
    """Resolve a deadline mentioned in class to epoch ms, relative to the day of the lecture."""
    if not text:
        return None
    import dateparser
    from dateparser.search import search_dates

    # dateparser resolves "Tuesday" but returns None for "next Tuesday" / "this Friday".
    text = WEEKDAY_QUALIFIER_RE.sub(r"\1", str(text).strip())
    options = {"RELATIVE_BASE": lecture_start, "PREFER_DATES_FROM": "future"}
    parsed = dateparser.parse(text, settings=options)
    if parsed is None:
        # The model sometimes returns a phrase ("quiz Friday") instead of a bare date.
        found = search_dates(text, settings=options, languages=["en"])
        parsed = found[0][1] if found else None
    return int(parsed.timestamp() * 1000) if parsed else None
