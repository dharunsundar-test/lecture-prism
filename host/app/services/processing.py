"""Per-chunk audio processing: noise reduction, transcription and loudness measurement."""
import math
import subprocess
import threading
from pathlib import Path
from app.core.config import settings


SAMPLE_RATE = 16000

_whisper_model = None
_whisper_lock = threading.Lock()


def reduce_noise(input_path: Path, output_path: Path) -> bool:
    """Apply basic noise reduction using ffmpeg."""
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", "highpass=f=80,lowpass=f=8000,anlmdn=s=3:p=0.002:r=10",
            "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
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
    """Transcribe audio using faster-whisper. Raises on failure; silence yields an empty list.
    Times are seconds from the start of this file."""
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


def add_loudness(audio_path: Path, segments: list[dict]) -> None:
    """Set rms_db on each segment (None if it can't be measured). Used to tell the lecturer
    from other speakers; failure here shouldn't fail the chunk."""
    try:
        from faster_whisper import decode_audio
        samples = decode_audio(str(audio_path), sampling_rate=SAMPLE_RATE)
    except Exception as e:
        print(f"Could not decode {audio_path} for loudness: {e}")
        for seg in segments:
            seg["rms_db"] = None
        return

    for seg in segments:
        seg["rms_db"] = rms_db(samples[int(seg["start"] * SAMPLE_RATE):int(seg["end"] * SAMPLE_RATE)])


def rms_db(samples) -> float | None:
    """RMS level in dBFS of float samples in [-1, 1]."""
    if len(samples) == 0:
        return None
    mean_square = float((samples.astype("float64") ** 2).mean())
    return round(10 * math.log10(mean_square + 1e-12), 2)
