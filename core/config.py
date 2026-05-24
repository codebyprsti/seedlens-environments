from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from project root (parent of core/) so it works regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Database (set via environment / .env; do not hardcode secrets)
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASS: str = ""
    DB_HOST: str = ""
    DB_PORT: str = "5432"

    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # Sentinel Hub (Copernicus Data Space)
    SH_CLIENT_ID: str = ""
    SH_CLIENT_SECRET: str = ""

settings = Settings()
