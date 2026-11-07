import logging
from typing import List, Union

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Spelled out rather than read from `logging.getLevelNamesMapping()` so the
# validator behaves identically on every supported interpreter.
_VALID_LOG_LEVELS = frozenset(
    {"CRITICAL", "ERROR", "FATAL", "WARNING", "WARN", "INFO", "DEBUG", "NOTSET"}
)

# B2: every rate-limit knob must be positive. A zero attempt budget blocks all
# traffic outright, while a zero or negative window makes the sliding-window
# accounting meaningless.
_RATE_LIMIT_POSITIVE_FIELDS = (
    "RATE_LIMIT_WORKER_COUNT",
    "RATE_LIMIT_LOGIN_ATTEMPTS",
    "RATE_LIMIT_LOGIN_WINDOW_SECONDS",
    "RATE_LIMIT_REGISTER_ATTEMPTS",
    "RATE_LIMIT_REGISTER_WINDOW_SECONDS",
    "RATE_LIMIT_GENERATIVE_ATTEMPTS",
    "RATE_LIMIT_GENERATIVE_WINDOW_SECONDS",
    "RATE_LIMIT_UPLOAD_ATTEMPTS",
    "RATE_LIMIT_UPLOAD_WINDOW_SECONDS",
)

# --- JWT signing key ------------------------------------------------------
# There is deliberately no default value. The app used to ship
# `development_secret_key_change_in_production`, and because JWTs are signed
# symmetrically, anyone who can read the repository could mint a valid token
# for an arbitrary user id and take over that account. The key is now required,
# and the historical placeholder is rejected outright so a copied `.env.example`
# cannot silently reintroduce it.
SECRET_KEY_MIN_LENGTH = 32
_SECRET_KEY_REJECTED = frozenset(
    {
        "development_secret_key_change_in_production",
        "changeme",
        "change-me",
        "change_me",
    }
)
# Substrings that mark a value as documentation filler rather than a real key.
_SECRET_KEY_PLACEHOLDER_SUBSTRINGS = (
    "changeme",
    "change_in_production",
    "change-in-production",
    "change_me",
    "your_",
    "your-",
    "replace_me",
    "replace-me",
    "placeholder",
    "insecure",
)
_SECRET_KEY_HINT = (
    'generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
)


def _validate_secret_key(value: str) -> str:
    """Fails closed on a missing, short, or placeholder signing key.

    Deliberately rejects in *every* environment rather than only in production.
    A development fallback is precisely what made the old default unsafe: the
    same committed value ended up signing tokens wherever the app was deployed,
    because nobody had configured anything.

    Reports the length but never the value, so a misconfiguration cannot put the
    key into a log line or a traceback.
    """
    key = (value or "").strip()

    if not key:
        raise ValueError(
            "SECRET_KEY is required and has no default: a committed default "
            "JWT signing key lets anyone forge a session for any account. "
            f"Set it in backend/.env -- {_SECRET_KEY_HINT}."
        )

    if key.lower() in _SECRET_KEY_REJECTED:
        raise ValueError(
            "SECRET_KEY is set to a well-known placeholder value that is "
            f"published in this repository; {_SECRET_KEY_HINT}."
        )

    if len(key) < SECRET_KEY_MIN_LENGTH:
        raise ValueError(
            f"SECRET_KEY must be at least {SECRET_KEY_MIN_LENGTH} characters "
            f"to carry enough entropy for HMAC-SHA256; got {len(key)}. "
            f"Regenerate it -- {_SECRET_KEY_HINT}."
        )

    lowered = key.lower()
    for marker in _SECRET_KEY_PLACEHOLDER_SUBSTRINGS:
        if marker in lowered:
            raise ValueError(
                "SECRET_KEY still contains placeholder text "
                f"({marker!r}) and would be guessable; "
                f"regenerate it -- {_SECRET_KEY_HINT}."
            )

    return key


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Learning Assistant API"
    ENVIRONMENT: str = "development"
    # A2: both flags default to False. `DEBUG` is only FastAPI's exception
    # detail switch; it no longer implies SQL logging, because SQLAlchemy's
    # `echo=True` writes statement *parameters* to the log -- password hashes,
    # JWT subjects, quiz answers, document text and embeddings included.
    DEBUG: bool = False
    SQL_ECHO: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant"

    # LLM / AI Keys
    GEMINI_API_KEY: str = ""
    # Must be a real, currently-served Gemini model id. An unrecognised id makes
    # every generative call raise, and because all three generators plus the
    # tutor fall back to a deterministic offline path, that failure is silent:
    # the app looks healthy while serving cloze deletions instead of grounded
    # model output. Override this in .env rather than relying on the default.
    GEMINI_QA_MODEL: str = "gemini-3.8-flash"
    # Embedding model and vector width. The width is a hard schema constraint:
    # `document_chunks.embedding` is Vector(768), so changing it requires both a
    # migration and a full re-embedding pass over every stored chunk.
    # gemini-embedding-2 defaults to 3072 dimensions; the service requests
    # EMBEDDING_DIMENSIONS explicitly via MRL truncation to keep the 768 column.
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_DIMENSIONS: int = 768
    # B1: whether a *deliberate* offline run may use hash-derived pseudo-vectors
    # instead of real Gemini embeddings. False by default, and false is the only
    # value a real deployment should have: with it false, an absent, placeholder,
    # rejected or erroring key raises instead of quietly returning a meaningless
    # vector, so a document can never reach status=READY holding noise. The test
    # suite sets it true explicitly via tests/conftest.py. Note this governs
    # embeddings only -- the generative services keep their own offline paths.
    EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK: bool = False

    # Observability
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # Security
    # Required -- see SECRET_KEY_MIN_LENGTH. Never ship a committed default.
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    # B1: the access token used to live for 24 hours, so a token that leaked --
    # out of a shared machine, a proxy log, a browser extension -- stayed usable
    # for a full day and could not be withdrawn. It is now a short-lived
    # credential that is checked against server-side session state on every
    # request, and the long-lived credential is an opaque refresh token that the
    # server stores only as a SHA-256 digest and can revoke individually.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Work factor for pbkdf2_sha256. The passlib default of 29,000 is roughly 20x
    # below current OWASP guidance, so it is set explicitly here and carried in
    # the hash string itself, which keeps older hashes verifiable.
    PBKDF2_ROUNDS: int = 600_000
    PBKDF2_MIN_ROUNDS: int = 10_000

    # B2: browser session cookies. The access token is delivered in an HttpOnly
    # cookie so application JavaScript never holds a credential that a single XSS
    # can read out of localStorage and exfiltrate.
    AUTH_COOKIE_SECURE: bool = False
    # "lax" (or "strict") keeps the cookie off cross-site requests, which is what
    # makes the double-submit CSRF token below a second line of defence rather
    # than the only one. "none" is the correct choice only when the frontend and
    # API are on different *sites* (not just different ports); it is rejected
    # outside production because browsers require Secure with it, and an HTTP
    # localhost deployment cannot set a usable cookie that way.
    AUTH_COOKIE_SAMESITE: str = "lax"
    # Left empty so the cookie is host-only by default, which is the tightest
    # option. Set it only to share a session across subdomains of one parent.
    AUTH_COOKIE_DOMAIN: str = ""
    # B2: double-submit CSRF token, enforced for cookie-authenticated unsafe
    # requests. A header cannot be set by a cross-origin form post or image
    # load, so an attacker who cannot read the cookie cannot produce a matching
    # header value.
    CSRF_PROTECTION_ENABLED: bool = True

    # Rate limiting (in-process sliding window; see app/core/rate_limit.py)
    RATE_LIMIT_ENABLED: bool = True
    # B2: how many uvicorn workers this deployment will run. The counters live in
    # each worker's own memory, so the limit a single caller actually faces is
    # RATE_LIMIT_*_ATTEMPTS multiplied by this number. It is declared rather than
    # detected because there is no portable way to discover the worker count from
    # inside the process; declaring anything above 1 makes the app log the
    # weakened limit at startup instead of leaving it to be discovered. A value
    # above 1 also needs a shared counter store (Redis) to be a real global limit.
    RATE_LIMIT_WORKER_COUNT: int = 1
    RATE_LIMIT_LOGIN_ATTEMPTS: int = 10
    RATE_LIMIT_LOGIN_WINDOW_SECONDS: int = 60
    RATE_LIMIT_REGISTER_ATTEMPTS: int = 5
    RATE_LIMIT_REGISTER_WINDOW_SECONDS: int = 300
    RATE_LIMIT_GENERATIVE_ATTEMPTS: int = 30
    RATE_LIMIT_GENERATIVE_WINDOW_SECONDS: int = 60
    RATE_LIMIT_UPLOAD_ATTEMPTS: int = 10
    RATE_LIMIT_UPLOAD_WINDOW_SECONDS: int = 3600

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

    @field_validator("PBKDF2_ROUNDS")
    @classmethod
    def validate_pbkdf2_rounds(cls, v: int) -> int:
        """Rejects a work factor low enough to be brute-forced cheaply.

        The floor is well under the 600,000 default on purpose: it exists to
        catch a typo such as ``PBKDF2_ROUNDS=60`` rather than to forbid tuning
        for a throwaway test environment.
        """
        if v < cls.model_fields["PBKDF2_MIN_ROUNDS"].default:
            raise ValueError(
                f"PBKDF2_ROUNDS is implausibly low; use at least "
                f"{cls.model_fields['PBKDF2_MIN_ROUNDS'].default}."
            )
        return v

    @field_validator(*_RATE_LIMIT_POSITIVE_FIELDS)
    @classmethod
    def validate_rate_limit_positive(cls, v: int, info: ValidationInfo) -> int:
        """Rejects a zero or negative rate-limit value.

        The message names the offending field but never echoes the configured
        value, so a bad setting cannot leak into a startup log or traceback.
        """
        if v < 1:
            raise ValueError(
                f"{info.field_name} must be at least 1; zero or negative values "
                "make the rate limit unsafe."
            )
        return v

    @field_validator("AUTH_COOKIE_SAMESITE", mode="before")
    @classmethod
    def normalize_cookie_samesite(cls, v: str, info: ValidationInfo) -> str:
        value = str(v).strip().lower()
        if value not in {"lax", "strict", "none"}:
            raise ValueError(
                "AUTH_COOKIE_SAMESITE must be one of 'lax', 'strict' or 'none'; "
                f"got {v!r}."
            )
        secure = bool(info.data.get("AUTH_COOKIE_SECURE", False))
        environment = str(info.data.get("ENVIRONMENT", "")).strip().lower()
        production = environment in {"production", "prod"}
        if value == "none" and not secure and not production:
            # 'none' without Secure is silently downgraded by browsers, which
            # produces a cookie that appears to work and then does not.
            raise ValueError(
                "AUTH_COOKIE_SAMESITE='none' requires AUTH_COOKIE_SECURE=true, "
                "otherwise browsers drop the cookie."
            )
        return value

    def __init__(self, **values):
        super().__init__(**values)
        # Checked here rather than in a field_validator on purpose. Pydantic
        # renders `input_value=<the raw input>` into ValidationError, so a
        # validator that rejects a weak key would print that key into whatever
        # captured the startup traceback. Raising outside validation keeps the
        # message informative and the secret out of the log.
        self.SECRET_KEY = _validate_secret_key(self.SECRET_KEY)
        if self.is_production:
            # B2: an auth cookie issued without `Secure` is sent over plaintext
            # HTTP and is captured by anyone on the path. Production is an HTTPS
            # deployment by definition, so this is forced rather than trusted to
            # the operator's .env.
            self.AUTH_COOKIE_SECURE = True


    def __repr__(self) -> str:
        """Masks the signing key.

        Settings is a pydantic model, so the default repr would print every
        field -- including SECRET_KEY -- into any log line, traceback or
        debugger session that touches the object. Only SECRET_KEY is redacted
        here; DATABASE_URL also embeds a password and is left alone to keep this
        change scoped to the signing-key fix.
        """
        shown = ", ".join(
            "SECRET_KEY='***'" if name == "SECRET_KEY" else f"{name}={value!r}"
            for name, value in self.__dict__.items()
        )
        return f"{type(self).__name__}({shown})"

    __str__ = __repr__

    @property
    def is_production(self) -> bool:
        """True when running as a deployed service rather than a workstation."""
        return self.ENVIRONMENT.strip().lower() in {"production", "prod"}

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
