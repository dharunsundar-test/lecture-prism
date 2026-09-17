import hashlib
import subprocess
from pathlib import Path


def chunk_offsets(chunks: list[dict]) -> dict[str, int]:
    """Start of each chunk within the joined session audio, in ms.

    Offsets come from the chunks' own durations rather than wall-clock times, so pauses
    between chunks don't open gaps that the joined audio doesn't have."""
    offsets = {}
    position = 0
    for chunk in sorted(chunks, key=lambda c: c["seq"]):
        offsets[chunk["id"]] = position
        position += chunk["duration_ms"]
    return offsets


def build_session_audio(session_id: str, chunks: list[dict], audio_dir: Path) -> Path:
    """Join a session's chunks, in order, into one file (cached until the set of chunks changes)."""
    present = [c for c in sorted(chunks, key=lambda c: c["seq"]) if Path(c["audio_path"]).exists()]
    if not present:
        raise FileNotFoundError(f"No audio chunks on disk for session {session_id}")
    if len(present) == 1:
        return Path(present[0]["audio_path"])

    session_dir = audio_dir / session_id
    fingerprint = "|".join(f"{c['seq']}:{c.get('sha256')}:{c['size_bytes']}" for c in present)
    key = hashlib.sha1(fingerprint.encode()).hexdigest()[:12]
    output = session_dir / f"full_{key}.m4a"
    if output.exists():
        return output

    for stale in session_dir.glob("full_*.m4a"):
        stale.unlink(missing_ok=True)

    list_file = session_dir / f"concat_{key}.txt"
    lines = []
    for c in present:
        path = Path(c["audio_path"]).resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{path}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")

    partial = session_dir / f"full_{key}.partial"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(partial)],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode(errors='replace')[-500:]}")
        partial.replace(output)
    finally:
        list_file.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return output


def probe_duration_ms(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return int(float(result.stdout.strip()) * 1000)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
