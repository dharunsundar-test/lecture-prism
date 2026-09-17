from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Path("./data")
    audio_dir: Path = Path("./data/audio")
    db_path: Path = Path("./data/lecture_capture.db")
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

for path in [settings.data_dir, settings.audio_dir]:
    path.mkdir(parents=True, exist_ok=True)