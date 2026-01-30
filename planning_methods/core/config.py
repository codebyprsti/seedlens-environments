from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DB_NAME="dev_seedworks_db"
    DB_USER="madanm"
    DB_PASS= "Prstilabdb1@"
    DB_HOST="10.8.0.1"
    DB_PORT=5432
    ENVIRONMENT="development"
    DEBUG="true"
    LOG_LEVEL="INFO"


settings = Settings()
