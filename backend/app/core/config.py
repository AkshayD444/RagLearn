import logging
from typing import List, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Spelled out rather than read from `logging.getLevelNamesMapping()` so the
# validator behaves identically on every supported interpreter.
_VALID_LOG_LEVELS = frozenset(
    {"CRITICAL", "ERROR", "FATAL", "WARNING", "WARN", "INFO", "DEBUG", "NOTSET"}
)


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Learning Assistant API"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant"

    # LLM / AI Keys
    GEMINI_API_KEY: str = ""
    # Must be a real, currently-served Gemini model id. An unrecognised id makes
    # every generative call raise, and because all three generators plus the
    # tutor fall back to a deterministic offline path, that failure is silent:
    # the app looks healthy while serving cloze deletions instead of grounded
    # model output. Override this in .env rather than relying on the default.
    GEMINI_QA_MODEL: str = "gemini-2.5-flash"
    # Embedding model and vector width. The width is a hard schema constraint:
    # `document_chunks.embedding` is Vector(768), so changing it requires both a
    # migration and a full re-embedding pass over every stored chunk.
    GEMINI_EMBEDDING_MODEL: str = "text-embedding-004"
    EMBEDDING_DIMENSIONS: int = 768

    # Observability
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # Security
    SECRET_KEY: str = "development_secret_key_change_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # CORS
    BACKEND_CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000"]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json

                return json.loads(v)
            return [i.strip() for i in v.split(",")]
        return v

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        """Rejects a typo'd level instead of silently falling back to INFO.

        ``LOG_LEVEL=verbose`` is an easy mistake; without this check the
        operator gets no logs and no indication why.
        """
        level = str(v).strip().upper()
        if not level.isdigit() and level not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be a standard logging level name or a numeric "
                f"level; got {v!r}."
            )
        return level

    @property
    def log_level_number(self) -> int:
        """Numeric form of ``LOG_LEVEL``, for ``logging.setLevel``."""
        if self.LOG_LEVEL.isdigit():
            return int(self.LOG_LEVEL)
        return int(getattr(logging, self.LOG_LEVEL, logging.INFO))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
