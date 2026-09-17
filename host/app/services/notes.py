import re
import time
from datetime import datetime
import requests
from app.core.config import settings


# Announcements and action items share a document, so group by document before generating.
CATEGORY_TO_DOC_TYPE = {
    "concept": "concepts",
    "example": "examples",
    "announcement": "announcements",
    "action_item": "announcements",
    "qa": "qa",
}

PROMPTS = {
    "concepts": "Summarize the key concepts and definitions from this lecture transcript. Use clear headings and bullet points.",
    "examples": "Extract and document all worked examples, problems solved, and demonstrations from this transcript.",
    "announcements": "List all announcements, action items, assignments, dates, deadlines, exam info, quiz dates, and administrative details mentioned. Give each deadline as an absolute date.",
    "qa": "Document all questions asked and answers given during Q&A sections.",
}

TIMESTAMP_INSTRUCTION = (
    "Each transcript line starts with a [m:ss] timestamp. Start every bullet point with the timestamp "
    "of the transcript line it is based on, copied exactly, for example: - [12:05] Definition of ..."
)

# Keeps each request well inside free-tier token limits; longer documents are generated in parts.
MAX_INPUT_CHARS = 20_000

TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):([0-5]\d)(?::([0-5]\d))?\]")
BULLET_RE = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")


class NotesError(Exception):
    pass


def generate_documents(session: dict, segments: list[dict], model: str) -> list[dict]:
    """segments must carry session-relative start_ms (as returned by the segments API)."""
    grouped: dict[str, list[dict]] = {}
    for seg in segments:
        doc_type = CATEGORY_TO_DOC_TYPE.get(seg["category"])
        if doc_type:
            grouped.setdefault(doc_type, []).append(seg)

    lecture_date = datetime.fromtimestamp(session["started_at"] / 1000)
    documents = []
    for doc_type, segs in grouped.items():
        lines = [f"[{format_ms(s['start_ms'])}] {s['source_text']}" for s in sorted(segs, key=lambda s: s["start_ms"])]
        system_prompt = (
            f"{PROMPTS[doc_type]}\n{TIMESTAMP_INSTRUCTION}\n"
            f"The lecture was recorded on {lecture_date:%A %Y-%m-%d}."
        )
        parts = [_complete(system_prompt, text, model) for text in split_lines(lines, MAX_INPUT_CHARS)]
        documents.append({
            "type": doc_type,
            "content": "\n\n".join(part.strip() for part in parts),
            "generated_at": int(time.time() * 1000),
            "model": model,
        })
    return documents


def split_lines(lines: list[str], max_chars: int) -> list[str]:
    """Group lines into texts of at most max_chars (a single longer line becomes its own text)."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > max_chars:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def parse_note_items(content: str) -> list[dict]:
    """Split generated notes into display items, pulling out each line's [m:ss] timestamp so the
    viewer can highlight the note being played and seek to it."""
    items = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue

        kind = "text"
        if line.startswith("#"):
            kind = "heading"
            line = line.lstrip("#").strip()
        elif BULLET_RE.match(line):
            kind = "item"
            line = BULLET_RE.sub("", line, count=1)

        start_ms = None
        match = TIMESTAMP_RE.search(line)
        if match:
            start_ms = _timestamp_ms(match)
            line = (line[:match.start()] + line[match.end():]).strip()
            # Tidy leftovers such as "**[12:05]** text" or "[12:05] - text".
            line = re.sub(r"^(?:\*\*\s*\*\*|[-–:]\s*)", "", line).strip()

        items.append({"kind": kind, "text": line, "start_ms": start_ms})
    return items


def _timestamp_ms(match: re.Match) -> int:
    first, second, third = match.group(1), match.group(2), match.group(3)
    if third is None:
        minutes, seconds = int(first), int(second)
        return (minutes * 60 + seconds) * 1000
    hours, minutes, seconds = int(first), int(second), int(third)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _complete(system_prompt: str, text: str, model: str) -> str:
    try:
        if model == "groq":
            if not settings.groq_api_key:
                raise NotesError("GROQ_API_KEY is not set in host/.env")
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                },
                timeout=120
            )
            if response.status_code == 429:
                raise NotesError("Groq rate limit reached; wait a minute and try again, or use the local model")
            _raise_for_status(response, "Groq")
            return response.json()["choices"][0]["message"]["content"]

        response = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={"model": settings.notes_model, "system": system_prompt, "prompt": text, "stream": False},
            timeout=600
        )
        _raise_for_status(response, "Ollama")
        return response.json()["response"]
    except requests.RequestException as e:
        raise NotesError(f"Notes request failed: {e}") from e


def _raise_for_status(response: requests.Response, provider: str):
    if response.status_code != 200:
        raise NotesError(f"{provider} returned {response.status_code}: {response.text[:300]}")


def format_ms(ms: int) -> str:
    total = ms // 1000
    return f"{total // 60}:{total % 60:02d}"
