from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

HOST_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Anchored to host/ so the server behaves the same whatever directory it is started from.
    # extra="ignore" keeps unrelated keys in .env from crashing startup.
    model_config = SettingsConfigDict(env_file=HOST_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = HOST_DIR / "data"
    log_level: str = "INFO"
    worker_enabled: bool = True

    ollama_url: str = "http://localhost:11434"
    classification_model: str = "llama3.1:8b"
    notes_model: str = "llama3.1:8b"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"

    # "auto" picks large-v3 when a CUDA device is available, medium otherwise.
    whisper_model: str = "auto"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lecture_capture.db"

    @property
    def pair_token_path(self) -> Path:
        return self.data_dir / "pair_token.txt"


settings = Settings()

for path in [settings.data_dir, settings.audio_dir, settings.transcripts_dir]:
    path.mkdir(parents=True, exist_ok=True)
