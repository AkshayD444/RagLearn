"""Password policy and hashing tests: A5 (strength) and A6 (work factor)."""

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.security import (
    get_password_hash,
    needs_rehash,
    verify_password,
)
from app.schemas.user import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, UserCreate, UserLogin

# The round count the app used to inherit from passlib. Used here to prove that
# hashes written by the previous configuration still verify.
LEGACY_ROUNDS = 29_000
PASSLIB_LEGACY_DEFAULT_ROUNDS = 29_000


# ---------------------------------------------------------------------------
# A5 -- registration password policy
# ---------------------------------------------------------------------------


def test_minimum_length_is_enforced():
    assert MIN_PASSWORD_LENGTH == 8

    with pytest.raises(ValidationError) as excinfo:
        UserCreate(email="a@example.com", full_name="A", password="x" * (MIN_PASSWORD_LENGTH - 1))

    assert "password" in str(excinfo.value).lower()


def test_exactly_the_minimum_length_is_accepted():
    """The boundary must be inclusive, not off by one."""
    user = UserCreate(
        email="a@example.com", full_name="A", password="x" * MIN_PASSWORD_LENGTH
    )
    assert len(user.password) == MIN_PASSWORD_LENGTH


def test_overlong_password_is_rejected():
    """Bounded so an unbounded body cannot reach the hasher."""
    with pytest.raises(ValidationError):
        UserCreate(
            email="a@example.com",
            full_name="A",
            password="x" * (MAX_PASSWORD_LENGTH + 1),
        )


@pytest.mark.parametrize("password", ["        ", "\t\t\t\t\t\t\t\t", "\n\n\n\n\n\n\n\n"])
def test_whitespace_only_password_is_rejected(password):
    """Eight spaces satisfies the length rule but is not a password."""
    with pytest.raises(ValidationError):
        UserCreate(email="a@example.com", full_name="A", password=password)


def test_no_composition_rules_are_imposed():
    """A long passphrase must be accepted.

    Deliberate: character-class rules push users toward predictable
    substitutions without adding entropy, and would reject legitimate
    passphrases.
    """
    user = UserCreate(email="a@example.com", full_name="A", password="correct horse battery staple")
    assert user.password == "correct horse battery staple"


def test_login_schema_is_not_length_bounded():
    """Login must not enforce the registration policy.

    Two reasons: an account registered before this policy existed must still be
    able to sign in, and rejecting short passwords at login would tell an
    unauthenticated prober which addresses have accounts.
    """
    login = UserLogin(email="a@example.com", password="x")
    assert login.password == "x"


# ---------------------------------------------------------------------------
# A6 -- hashing work factor
# ---------------------------------------------------------------------------


def test_new_hashes_use_the_configured_work_factor():
    hashed = get_password_hash("password123")
    assert hashed.startswith(f"$pbkdf2-sha256${settings.PBKDF2_ROUNDS}$")


def test_configured_work_factor_is_not_the_old_passlib_default():
    """The vulnerability was the implicit 29,000-round library default."""
    assert settings.PBKDF2_ROUNDS != PASSLIB_LEGACY_DEFAULT_ROUNDS
    assert settings.PBKDF2_ROUNDS >= 600_000


def test_hashing_round_trips():
    hashed = get_password_hash("password123")
    assert verify_password("password123", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_hashes_are_salted_so_equal_passwords_differ():
    first = get_password_hash("same-password")
    second = get_password_hash("same-password")
    assert first != second
    assert verify_password("same-password", first)
    assert verify_password("same-password", second)


def test_plaintext_never_appears_in_the_hash():
    """A regression guard on accidentally logging or storing the raw value."""
    hashed = get_password_hash("correct-horse-battery-staple")
    assert "correct-horse-battery-staple" not in hashed


def test_existing_legacy_hashes_still_verify():
    """A6 must not lock out accounts created under the old configuration.

    passlib carries the round count inside the hash string, so a 29,000-round
    hash remains verifiable with the stronger context in place.
    """
    from passlib.hash import pbkdf2_sha256

    legacy_hash = pbkdf2_sha256.using(rounds=LEGACY_ROUNDS).hash("password123")

    assert verify_password("password123", legacy_hash) is True
    assert verify_password("not-the-password", legacy_hash) is False


def test_legacy_hash_is_flagged_for_upgrade():
    from passlib.hash import pbkdf2_sha256

    legacy_hash = pbkdf2_sha256.using(rounds=LEGACY_ROUNDS).hash("password123")
    assert needs_rehash(legacy_hash) is True


def test_current_hash_is_not_flagged_for_upgrade():
    """Otherwise every login would rewrite the row for no reason."""
    assert needs_rehash(get_password_hash("password123")) is False


def test_unreadable_hash_is_not_flagged_for_rehash():
    """Rehashing is an upgrade path; an unknown format must be left alone.

    Rewriting a hash the app cannot parse would destroy the only copy of the
    credential, and verify_password rejects it anyway.
    """
    assert needs_rehash("not-a-real-hash") is False
    assert needs_rehash("") is False
    assert needs_rehash("$2b$12$bcryptstylehashisadifferentformat") is False


@pytest.mark.parametrize(
    "stored_hash",
    [
        "not-a-real-hash",
        "",
        "$pbkdf2-sha256$",
        "$pbkdf2-sha256$notanint$salt$sum",
        "$pbkdf2-sha256$600000$salt",
        "$pbkdf2-sha256$600000$salt$notvalidb64!!!",
        "$pbkdf2-sha256$0600000$salt$" + "A" * 43,
        # Valid schema, but rounds=0xffffffff overflows the PBKDF2 backend.
        "$pbkdf2-sha256$4294967295$salt$RMwvIIUOXVWGpdFcFGzMXTN3Rd1HqyJ9o67qH8nNfGM",
        "$2b$12$bcryptstylehashisadifferentformat",
        "$argon2id$v=19$m=65536,t=3,p=4$abc$def",
        "$1$salt$hash",
    ],
)
def test_verify_password_returns_false_for_unusable_stored_hashes(stored_hash):
    """Malformed/unsupported stored hashes must not become a 500 on login.

    Covers both ``UnknownHashError`` (an algorithm this context does not
    support) and the plain ``ValueError`` passlib raises for a recognised but
    corrupt pbkdf2 hash.
    """
    assert verify_password("password123", stored_hash) is False


def test_verify_password_returns_false_when_stored_rounds_overflow():
    """A huge-but-parseable rounds field must fail closed, not raise."""
    stored_hash = (
        "$pbkdf2-sha256$4294967295$salt$"
        "RMwvIIUOXVWGpdFcFGzMXTN3Rd1HqyJ9o67qH8nNfGM"
    )
    assert verify_password("password123", stored_hash) is False


@pytest.mark.parametrize("stored_hash", [None, 123, 1.5, object()])
def test_verify_password_returns_false_for_a_non_string_stored_hash(stored_hash):
    """Passlib raises TypeError for non-string hashes; a damaged row is a fail."""
    assert verify_password("password123", stored_hash) is False


def test_verify_password_returns_false_for_an_unknown_hash_error(monkeypatch):
    """An explicit passlib UnknownHashError from the stored hash is handled."""
    from passlib.exc import UnknownHashError

    import app.core.security as security

    def raise_unknown_hash(*_args, **_kwargs):
        raise UnknownHashError("hash could not be identified")

    monkeypatch.setattr(security.pwd_context, "verify", raise_unknown_hash)
    assert security.verify_password("password123", "$unknown$hash") is False


def test_verify_password_does_not_swallow_unexpected_errors(monkeypatch):
    """A broken backend or application bug must not look like a bad password."""
    import app.core.security as security

    def explode(*_args, **_kwargs):
        raise RuntimeError("password backend is broken")

    monkeypatch.setattr(security.pwd_context, "verify", explode)
    with pytest.raises(RuntimeError, match="password backend is broken"):
        security.verify_password("password123", "$pbkdf2-sha256$600000$salt$sum")


def test_verify_password_does_not_swallow_plaintext_type_errors():
    """Only the stored hash's type is guarded; bad plaintext input still raises."""
    hashed = get_password_hash("password123")
    with pytest.raises(TypeError):
        verify_password(None, hashed)


@pytest.mark.asyncio
async def test_malformed_hash_does_not_break_login(client):
    """End-to-end: a corrupt stored hash is a 401, not a 500.

    ``verify_password`` used to let passlib's exception escape on an unparseable
    stored hash, which surfaced as an unhandled 500 and leaked the traceback.
    """
    from app.models.user import User
    from tests.conftest import TestAsyncSessionLocal

    email = "corrupt-hash@example.com"
    async with TestAsyncSessionLocal() as session:
        session.add(
            User(
                email=email,
                full_name="Corrupt",
                hashed_password="definitely-not-a-valid-hash",
            )
        )
        await session.commit()

    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_upgrades_a_legacy_hash_in_place(client):
    """A successful login migrates an old hash onto the configured work factor.

    This is the migration path for A6: existing accounts keep working, and their
    stored hash is quietly strengthened the first time they sign in.
    """
    from passlib.hash import pbkdf2_sha256
    from sqlalchemy import select

    from app.core.security import needs_rehash as check_needs_rehash
    from app.models.user import User
    from tests.conftest import TestAsyncSessionLocal

    email = "legacy-hash@example.com"
    legacy_hash = pbkdf2_sha256.using(rounds=LEGACY_ROUNDS).hash("password123")

    async with TestAsyncSessionLocal() as session:
        session.add(
            User(email=email, full_name="Legacy", hashed_password=legacy_hash)
        )
        await session.commit()

    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 200, response.text

    async with TestAsyncSessionLocal() as session:
        stored = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()

    assert stored.hashed_password != legacy_hash
    assert check_needs_rehash(stored.hashed_password) is False
    assert f"${settings.PBKDF2_ROUNDS}$" in stored.hashed_password
    # The upgraded hash still authenticates.
    assert verify_password("password123", stored.hashed_password) is True


@pytest.mark.asyncio
async def test_login_does_not_rewrite_an_up_to_date_hash(client, registered_user):
    """A current-strength hash must be left alone, not rewritten every login.

    A needless rewrite on every sign-in would be pure write amplification, so
    this is what proves the ``needs_rehash`` gate is doing its job in the
    negative direction too.
    """
    from sqlalchemy import select

    from app.models.user import User
    from tests.conftest import TestAsyncSessionLocal

    async def stored_hash() -> str:
        async with TestAsyncSessionLocal() as session:
            return (
                await session.execute(
                    select(User).where(User.email == registered_user["email"])
                )
            ).scalar_one().hashed_password

    before = await stored_hash()
    assert needs_rehash(before) is False

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": "password123"},
    )
    assert response.status_code == 200, response.text

    assert await stored_hash() == before
