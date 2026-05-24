from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database (set via environment; do not hardcode secrets)
    DB_NAME = ""
    DB_USER = ""
    DB_PASS = ""
    DB_HOST = ""
    DB_PORT = 5432
    ENVIRONMENT="development"
    DEBUG="true"
    LOG_LEVEL="INFO"


settings = Settings()
