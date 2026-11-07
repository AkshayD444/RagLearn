"""Configuration-hardening tests: A1 (signing key), A2 (safe logging defaults).

These construct ``Settings`` directly rather than patching the module-level
singleton, because the behaviour under test is "does constructing Settings
reject this?" -- which happens at import time in production and is impossible
to exercise on an already-imported object.
"""

import pytest
from pydantic import ValidationError

from app.core.config import SECRET_KEY_MIN_LENGTH, Settings

VALID_KEY = "k" * 64

# SECRET_KEY is validated in ``Settings.__init__`` rather than in a pydantic
# field validator, so the failure is a plain ValueError. That is deliberate:
# pydantic's ValidationError renders ``input_value=<raw input>``, which would
# print the rejected key into the startup traceback. See
# test_rejected_secret_key_never_appears_in_the_error.


# ---------------------------------------------------------------------------
# A1 -- JWT signing key
# ---------------------------------------------------------------------------


def test_secret_key_has_no_usable_default():
    """The field must ship empty, so an unconfigured app cannot start.

    A committed default was the original vulnerability: JWTs are signed
    symmetrically, so the value published in the repository let anyone mint a
    valid token for any user id.
    """
    assert Settings.model_fields["SECRET_KEY"].default == ""


def test_missing_secret_key_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        Settings(_env_file=None, SECRET_KEY="")

    message = str(excinfo.value)
    assert "SECRET_KEY is required" in message
    # The hint must tell the operator how to fix it, without echoing a value.
    assert "token_urlsafe" in message


def test_historical_development_placeholder_is_rejected():
    """The exact value the repository used to ship must never come back."""
    with pytest.raises(ValueError) as excinfo:
        Settings(
            _env_file=None,
            SECRET_KEY="development_secret_key_change_in_production",
        )

    assert "well-known placeholder" in str(excinfo.value)


@pytest.mark.parametrize(
    "placeholder",
    [
        "changeme",
        "CHANGE_ME",
        "your-secret-key-goes-right-here",
        "replace-me-with-a-real-key",
        "super-secret-placeholder-value-01",
    ],
)
def test_placeholder_looking_keys_are_rejected(placeholder):
    """Catches a half-finished copy of .env.example, even when it is long."""
    with pytest.raises(ValueError):
        Settings(_env_file=None, SECRET_KEY=placeholder)


def test_short_secret_key_is_rejected():
    """Below 32 characters there is not enough entropy for HMAC-SHA256."""
    with pytest.raises(ValueError) as excinfo:
        Settings(_env_file=None, SECRET_KEY="a" * (SECRET_KEY_MIN_LENGTH - 1))

    message = str(excinfo.value)
    assert "at least" in message
    # The length is safe to report; the value itself must never appear.
    assert "a" * (SECRET_KEY_MIN_LENGTH - 1) not in message


def test_valid_secret_key_is_accepted():
    assert Settings(_env_file=None, SECRET_KEY=VALID_KEY).SECRET_KEY == VALID_KEY


def test_secret_key_is_stripped_of_surrounding_whitespace():
    """A value pasted with a trailing newline must not silently alter the key."""
    assert Settings(_env_file=None, SECRET_KEY=f"  {VALID_KEY}\n").SECRET_KEY == VALID_KEY


def test_secret_key_error_never_leaks_the_configured_value():
    """Even a rejected key must not be echoed into a log or a traceback."""
    leaky = "leaky-" + ("x" * 40) + "-changeme-tail"
    with pytest.raises(ValueError) as excinfo:
        Settings(_env_file=None, SECRET_KEY=leaky)

    assert leaky not in str(excinfo.value)


def test_rejected_secret_key_never_appears_in_the_error():
    """A rejected key must not reach the log via the exception's own text.

    This is why the check lives in ``__init__`` and not in a field validator:
    pydantic's ValidationError includes ``input_value``, which for this field is
    the key itself.
    """
    rejected = "leaky-changeme-" + ("x" * 40)
    with pytest.raises(ValueError) as excinfo:
        Settings(_env_file=None, SECRET_KEY=rejected)

    rendered = f"{excinfo.value!r} {excinfo.value}"
    assert rejected not in rendered
    assert "placeholder" in rendered


def test_settings_repr_masks_the_secret_key():
    """Printing the settings object must not dump the signing key.

    Only SECRET_KEY is masked. DATABASE_URL also embeds a password, but redacting
    that is outside this change and is listed as a follow-up rather than
    silently widening the diff.
    """
    configured = Settings(_env_file=None, SECRET_KEY=VALID_KEY)

    rendered = repr(configured)
    assert VALID_KEY not in rendered
    assert "SECRET_KEY='***'" in rendered
    assert str(configured) == rendered


def test_secret_key_is_required_in_production_too():
    """A production environment is not a special case -- it still needs a key.

    Gating on ENVIRONMENT is what allowed the old default to reach a deployed
    instance in the first place, so the check is unconditional.
    """
    with pytest.raises(ValueError):
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY="")


# ---------------------------------------------------------------------------
# A2 -- DEBUG and SQL_ECHO defaults
# ---------------------------------------------------------------------------


def test_debug_defaults_to_false():
    assert Settings.model_fields["DEBUG"].default is False
    assert Settings(_env_file=None, SECRET_KEY=VALID_KEY).DEBUG is False


def test_sql_echo_defaults_to_false():
    """SQLAlchemy `echo` must be off: it logs bound parameters, not just SQL."""
    assert Settings.model_fields["SQL_ECHO"].default is False
    assert Settings(_env_file=None, SECRET_KEY=VALID_KEY).SQL_ECHO is False


def test_running_outside_production_does_not_imply_sql_echo():
    """`ENVIRONMENT=development` must not switch SQL parameter logging back on."""
    dev = Settings(_env_file=None, ENVIRONMENT="development", SECRET_KEY=VALID_KEY)
    assert dev.is_production is False
    assert dev.SQL_ECHO is False


def test_debug_does_not_drive_sql_echo():
    """DEBUG=True is a debugging aid; it must stay independent of SQL logging."""
    noisy = Settings(_env_file=None, SECRET_KEY=VALID_KEY, DEBUG=True)
    assert noisy.DEBUG is True
    assert noisy.SQL_ECHO is False


def test_sql_echo_is_still_available_when_asked_for_explicitly():
    """Development debugging must remain possible via an explicit opt-in."""
    assert Settings(_env_file=None, SECRET_KEY=VALID_KEY, SQL_ECHO=True).SQL_ECHO is True


def test_engine_does_not_echo_sql_by_default():
    """The real engine, not just the settings object, must be silent."""
    from app.core.database import engine

    assert engine.echo is False


def test_app_is_not_in_debug_mode_by_default():
    from app.main import app

    assert app.debug is False


# ---------------------------------------------------------------------------
# PBKDF2 floor
# ---------------------------------------------------------------------------


def test_implausibly_low_pbkdf2_rounds_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, SECRET_KEY=VALID_KEY, PBKDF2_ROUNDS=60)

    assert "PBKDF2_ROUNDS" in str(excinfo.value)


def test_pbkdf2_rounds_default_is_materially_above_the_passlib_default():
    """The old implicit passlib default was 29,000."""
    assert Settings(_env_file=None, SECRET_KEY=VALID_KEY).PBKDF2_ROUNDS == 600_000


# ---------------------------------------------------------------------------
# B2 -- rate-limit settings must be positive
# ---------------------------------------------------------------------------

RATE_LIMIT_DEFAULTS = {
    "RATE_LIMIT_WORKER_COUNT": 1,
    "RATE_LIMIT_LOGIN_ATTEMPTS": 10,
    "RATE_LIMIT_LOGIN_WINDOW_SECONDS": 60,
    "RATE_LIMIT_REGISTER_ATTEMPTS": 5,
    "RATE_LIMIT_REGISTER_WINDOW_SECONDS": 300,
    "RATE_LIMIT_GENERATIVE_ATTEMPTS": 30,
    "RATE_LIMIT_GENERATIVE_WINDOW_SECONDS": 60,
    "RATE_LIMIT_UPLOAD_ATTEMPTS": 10,
    "RATE_LIMIT_UPLOAD_WINDOW_SECONDS": 3600,
}


@pytest.mark.parametrize("field", RATE_LIMIT_DEFAULTS)
@pytest.mark.parametrize("value", [0, -1, -999])
def test_rate_limit_settings_reject_non_positive_values(field, value):
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, SECRET_KEY=VALID_KEY, **{field: value})

    message = str(excinfo.value)
    assert field in message
    assert "at least 1" in message


@pytest.mark.parametrize("field,expected", RATE_LIMIT_DEFAULTS.items())
def test_rate_limit_settings_have_valid_defaults(field, expected):
    assert Settings.model_fields[field].default == expected

    configured = Settings(_env_file=None, SECRET_KEY=VALID_KEY)
    assert getattr(configured, field) == expected
    assert getattr(configured, field) >= 1


def test_rate_limit_validation_error_is_generic():
    """The validator message must not echo the rejected configuration value."""
    field = "RATE_LIMIT_LOGIN_ATTEMPTS"
    rejected = -987654
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, SECRET_KEY=VALID_KEY, **{field: rejected})

    (error,) = excinfo.value.errors()
    assert field in error["msg"]
    assert "at least 1" in error["msg"]
    assert str(rejected) not in error["msg"]
