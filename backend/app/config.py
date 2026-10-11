"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from .env file."""

    # Database
    database_url: str = "postgresql://user:password@localhost:5432/technical_interviewer"

    # Application
    secret_key: str = "dev-secret-key"
    debug: bool = True
    environment: str = "development"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Demo-only interviewer gate (NOT production authentication). Empty = interviewer access disabled.
    interviewer_passcode: str = ""

    # Retention policy for interview data, in days after an interview is completed. This is a POLICY value:
    # nothing is deleted automatically; the retention_cleanup.py tool lists (and, only on request, deletes) what is past it.
    data_retention_days: int = 180

    # Voice/Video
    voice_provider: str = "mock"

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
