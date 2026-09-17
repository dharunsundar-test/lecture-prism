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
        text = "\n".join(f"[{_format_ms(s['start_ms'])}] {s['source_text']}" for s in sorted(segs, key=lambda s: s["start_ms"]))
        system_prompt = f"{PROMPTS[doc_type]}\nThe lecture was recorded on {lecture_date:%A %Y-%m-%d}."
        documents.append({
            "type": doc_type,
            "content": _complete(system_prompt, text, model),
            "generated_at": int(time.time() * 1000),
            "model": model,
        })
    return documents


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


def _format_ms(ms: int) -> str:
    total = ms // 1000
    return f"{total // 60}:{total % 60:02d}"
