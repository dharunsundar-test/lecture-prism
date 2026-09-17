import subprocess
import json
import re
import threading
from datetime import datetime
from pathlib import Path
import requests
from app.core.config import settings


CATEGORIES = {"concept", "example", "announcement", "qa", "action_item", "filler"}
WEEKDAY_QUALIFIER_RE = re.compile(
    r"\b(?:next|this|coming|upcoming)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_whisper_model = None
_whisper_lock = threading.Lock()


def reduce_noise(input_path: Path, output_path: Path) -> bool:
    """Apply basic noise reduction using ffmpeg."""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", "highpass=f=80,lowpass=f=8000,anlmdn=s=3:p=0.002:r=10",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        return result.returncode == 0
    except Exception as e:
        print(f"Noise reduction failed: {e}")
        return False


def _get_whisper_model():
    """Load the model once; loading large-v3 takes far longer than transcribing a chunk."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            import ctranslate2
            from faster_whisper import WhisperModel

            has_gpu = ctranslate2.get_cuda_device_count() > 0
            size = settings.whisper_model
            if size == "auto":
                size = "large-v3" if has_gpu else "medium"
            print(f"Loading faster-whisper {size} on {'cuda' if has_gpu else 'cpu'}")
            _whisper_model = WhisperModel(
                size,
                device="cuda" if has_gpu else "cpu",
                compute_type="float16" if has_gpu else "int8",
            )
        return _whisper_model


def transcribe_audio(audio_path: Path) -> list[dict]:
    """Transcribe audio using faster-whisper. Raises on failure; silence yields an empty list."""
    segments, _ = _get_whisper_model().transcribe(
        str(audio_path),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )

    result = []
    for seg in segments:
        words = []
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "probability": w.probability
                })
        result.append({
            "id": seg.id,
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "words": words
        })
    return result


def detect_speaker_roles(segments: list[dict]) -> list[dict]:
    """Simple heuristic: long continuous segments = lecturer, short = other."""
    if not segments:
        return []

    results = []
    current_speaker = "lecturer"
    last_end = 0

    for seg in segments:
        duration = seg["end"] - seg["start"]
        gap = seg["start"] - last_end

        if gap > 2.0:
            current_speaker = "lecturer"
        elif duration < 3.0 and gap < 1.0:
            current_speaker = "other"

        results.append({**seg, "speaker": current_speaker})
        last_end = seg["end"]

    return results


def classify_segments(segments: list[dict], lecture_start: datetime) -> list[dict]:
    """Classify segments into 6 categories using the local LLM. Raises if the model's answer
    can't be used, so the chunk is marked failed instead of silently becoming all filler."""
    response = requests.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.classification_model,
            "prompt": _build_classification_prompt(segments, lecture_start),
            "format": "json",
            "stream": False,
        },
        timeout=600
    )
    response.raise_for_status()
    result = json.loads(response.json()["response"])
    return _merge_classifications(segments, _extract_items(result))


def _build_classification_prompt(segments: list[dict], lecture_start: datetime) -> str:
    text = "\n".join(f"[{i}] {s['speaker'].upper()}: {s['text']}" for i, s in enumerate(segments))
    return f"""This lecture was recorded on {lecture_start:%A %Y-%m-%d}.
Classify each transcript segment into ONE category: concept, example, announcement, qa, action_item, filler.
Use announcement for quiz, exam, seminar and other dates; action_item for assignments and tasks.
For announcements and action items that mention a date, set "deadline" to that date as YYYY-MM-DD,
resolving relative dates such as "next Tuesday" against the recording date. Otherwise use null.

Return a JSON object with one entry per segment index:
{{"segments": [{{"index": 0, "category": "concept", "summary": "...", "deadline": null}}]}}

Segments:
{text}"""


def _extract_items(result) -> list[dict]:
    # Ollama's JSON mode always returns an object, but models vary in how they wrap the list.
    if isinstance(result, dict):
        if isinstance(result.get("segments"), list):
            return result["segments"]
        if "index" in result:
            return [result]
        lists = [v for v in result.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    elif isinstance(result, list):
        return result
    raise ValueError(f"Classifier returned an unexpected JSON shape: {str(result)[:200]}")


def _merge_classifications(segments: list[dict], classifications: list[dict]) -> list[dict]:
    by_index = {}
    for c in classifications:
        try:
            by_index[int(c["index"])] = c
        except (KeyError, TypeError, ValueError):
            continue
    if segments and not by_index:
        raise ValueError("Classifier returned no usable segment indexes")

    merged = []
    for i, seg in enumerate(segments):
        c = by_index.get(i, _default_classification(seg))
        category = c.get("category") if c.get("category") in CATEGORIES else "filler"
        merged.append({**seg, "category": category, "summary": c.get("summary"), "deadline": c.get("deadline")})
    return merged


def _default_classification(seg: dict) -> dict:
    return {
        "category": "filler",
        "summary": seg["text"][:100],
        "deadline": None
    }


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
