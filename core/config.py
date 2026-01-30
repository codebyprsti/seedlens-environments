from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DB_NAME: str = "test_seedworks_db"
    DB_USER: str = "madanm"
    DB_PASS: str = "Prstilabdb1@"
    DB_HOST: str = "10.8.0.1"
    DB_PORT: str = "5432"

    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

settings = Settings()
