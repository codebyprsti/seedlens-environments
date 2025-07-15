from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DB_NAME: str = "SeedWorksDB"
    DB_USER: str = "apps"
    DB_PASS: str = "PAI_Uat1_Apps"
    DB_HOST: str = "prstiai-client-dev-db-instance.cl6gqami6ntb.ap-south-1.rds.amazonaws.com"
    DB_PORT: str = "5432"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

settings = Settings()
