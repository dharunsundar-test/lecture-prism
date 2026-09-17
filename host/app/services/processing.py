import subprocess
import json
import os
from pathlib import Path
from typing import Optional
from app.core.config import settings


def reduce_noise(input_path: Path, output_path: Path) -> bool:
    """Apply basic noise reduction using ffmpeg."""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", "highpass=f=80,lowpass=f=8000,anlmdn=s=3:p=0.002:r=10",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        return result.returncode == 0
    except Exception as e:
        print(f"Noise reduction failed: {e}")
        return False


def get_audio_duration(path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def transcribe_audio(audio_path: Path, model_size: str = "medium") -> list[dict]:
    """Transcribe audio using faster-whisper."""
    try:
        from faster_whisper import WhisperModel

        device = "cuda" if _has_gpu() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments, _ = model.transcribe(
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
    except Exception as e:
        print(f"Transcription failed: {e}")
        return []


def _has_gpu() -> bool:
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


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


def classify_segments(segments: list[dict]) -> list[dict]:
    """Classify segments into 6 categories using local LLM."""
    try:
        import requests

        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        model = os.getenv("CLASSIFICATION_MODEL", "llama3.1:8b")

        prompt = _build_classification_prompt(segments)

        response = requests.post(
            f"{ollama_url}/api/generate",
            json={"model": model, "prompt": prompt, "format": "json", "stream": False},
            timeout=120
        )

        if response.status_code == 200:
            result = json.loads(response.json()["response"])
            return _merge_classifications(segments, result)
    except Exception as e:
        print(f"Classification failed: {e}")

    return [_default_classification(seg) for seg in segments]


def _build_classification_prompt(segments: list[dict]) -> str:
    text = "\n".join(f"[{i}] {s['speaker'].upper()}: {s['text']}" for i, s in enumerate(segments))
    return f"""Classify each segment into ONE category: concept, example, announcement, qa, action_item, filler.
Return JSON array: [{{"index": 0, "category": "concept", "summary": "...", "deadline": "YYYY-MM-DD"}}, ...]

Segments:
{text}"""


def _merge_classifications(segments: list[dict], classifications: list[dict]) -> list[dict]:
    by_index = {c["index"]: c for c in classifications}
    return [
        {**seg, **by_index.get(i, _default_classification(seg))}
        for i, seg in enumerate(segments)
    ]


def _default_classification(seg: dict) -> dict:
    return {
        "category": "filler",
        "summary": seg["text"][:100],
        "deadline": None
    }


def extract_deadlines(text: str) -> list[str]:
    """Extract date references from text using dateparser."""
    try:
        import dateparser
        import re

        dates = []
        for match in re.finditer(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\w+ \d{1,2},? \d{4}\b|\b(next|this|coming) (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', text, re.IGNORECASE):
            parsed = dateparser.parse(match.group(), settings={'PREFER_DATES_FROM': 'future'})
            if parsed:
                dates.append(parsed.isoformat())
        return list(set(dates))
    except Exception:
        return []