"""B1/B2: the session/token lifecycle and the cookie transport that carries it.

Grouped by the behaviour being pinned down rather than by module:

* a valid token works, and an expired or malformed one is a 401;
* a token the server has withdrawn is a 401, whether it was withdrawn by a
  logout, by a password change, or by never having carried a ``ver`` claim;
* refresh tokens rotate, are single-use, and are stored only as a digest;
* the browser-facing surface sets HttpOnly cookies and refuses a
  cookie-authenticated write that has no CSRF header.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.core.auth_cookies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    REFRESH_COOKIE,
)
from app.core.config import settings
from app.core.database import Base
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.main import app
from app.models.refresh_token import RefreshToken
from app.models.user import User

PASSWORD = "password123"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client, email: str | None = None) -> dict:
    email = email or f"session-{uuid.uuid4().hex[:12]}@example.com"
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Test Student", "password": PASSWORD},
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Access token: validity, expiry, and the short lifetime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_freshly_issued_token_authenticates(client):
    data = await _register(client)

    res = await client.get("/api/v1/auth/me", headers=_auth(data["access_token"]))

    assert res.status_code == 200
    assert res.json()["id"] == data["user"]["id"]


def test_the_access_token_records_the_account_session_version():
    """The claim is what makes a token withdrawable at all."""
    token = create_access_token(subject=uuid.uuid4(), token_version=7)
    payload = decode_access_token(token)

    assert payload["ver"] == 7
    assert payload["typ"] == ACCESS_TOKEN_TYPE


def test_the_access_token_lifetime_is_far_shorter_than_a_day():
    """Guards the headline fix: 24h down to something that limits a leak."""
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS <= 30


@pytest.mark.asyncio
async def test_an_expired_token_is_rejected(client):
    data = await _register(client)
    expired = jwt.encode(
        {
            "sub": data["user"]["id"],
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            "typ": ACCESS_TOKEN_TYPE,
            # Present and correct, so the rejection is attributable to expiry and
            # not to the session check.
            "ver": 0,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    res = await client.get("/api/v1/auth/me", headers=_auth(expired))

    assert res.status_code == 401


@pytest.mark.asyncio
async def test_a_token_with_no_session_claim_is_rejected(client):
    """A pre-B1 token is withdrawn rather than honoured until it expires.

    Keeping it valid would leave exactly the 24-hour tokens this change exists to
    shorten usable for the rest of their life.
    """
    data = await _register(client)
    legacy = jwt.encode(
        {
            "sub": data["user"]["id"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    res = await client.get("/api/v1/auth/me", headers=_auth(legacy))

    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Server-side invalidation
# ---------------------------------------------------------------------------


async def _bump_token_version(session: AsyncSession, user_id: str) -> None:
    user = (
        await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    ).scalar_one()
    user.token_version += 1
    await session.commit()


@pytest.mark.asyncio
async def test_a_token_is_rejected_once_the_session_version_moves_on(client, db_session):
    data = await _register(client)
    token = data["access_token"]
    assert (await client.get("/api/v1/auth/me", headers=_auth(token))).status_code == 200

    await _bump_token_version(db_session, data["user"]["id"])

    res = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_password_change_invalidates_every_outstanding_token(client):
    data = await _register(client)
    old_access = data["access_token"]
    old_refresh = data["refresh_token"]

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a-new-password-1"},
        headers=_auth(old_access),
    )
    assert changed.status_code == 200, changed.text

    # The device that made the change is signed in again...
    assert changed.json()["access_token"] != old_access
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    # ...but nothing issued before the change can be used or renewed.
    assert (await client.get("/api/v1/auth/me", headers=_auth(old_access))).status_code == 401
    replayed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert replayed.status_code == 401


@pytest.mark.asyncio
async def test_password_change_requires_the_current_password(client):
    data = await _register(client)

    res = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "another-one-1"},
        headers=_auth(data["access_token"]),
    )

    assert res.status_code == 400
    # A rejected change must not have withdrawn the session.
    assert (await client.get("/api/v1/auth/me", headers=_auth(data["access_token"]))).status_code == 200


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_the_refresh_token(client):
    data = await _register(client)
    refresh = data["refresh_token"]

    res = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert res.status_code == 204

    replayed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert replayed.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_the_session_cookies(client):
    await _register(client)

    res = await client.post("/api/v1/auth/logout")

    assert res.status_code == 204
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        assert client.cookies.get(name) in (None, ""), name


@pytest.mark.asyncio
async def test_logout_only_ends_the_session_it_is_given(client, anonymous_client):
    """Signing out on one device must not sign the student out everywhere."""
    email = f"twice-{uuid.uuid4().hex[:10]}@example.com"
    first = await _register(client, email=email)
    # A second, independent session for the same account.
    again = await anonymous_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert again.status_code == 200, again.text
    second = again.json()

    res = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]}
    )
    assert res.status_code == 204

    still_valid = await anonymous_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert still_valid.status_code == 200


# ---------------------------------------------------------------------------
# Refresh rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_issues_a_new_pair_and_spends_the_old_one(client):
    data = await _register(client)

    res = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]}
    )

    assert res.status_code == 200, res.text
    body = res.json()
    # The refresh token is what must change. The access token is a function of
    # (subject, session version, second), so a refresh inside the same second can
    # legitimately be byte-identical; nothing about it is per-session.
    assert body["refresh_token"] != data["refresh_token"]
    assert body["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert (await client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))).status_code == 200

    # Rotation is what makes a stolen refresh token detectable: the copy that
    # loses the race is refused rather than quietly renewing the session.
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]}
    )
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_an_unknown_token(client):
    res = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": generate_refresh_token()}
    )

    assert res.status_code == 401


@pytest.mark.asyncio
async def test_concurrent_refresh_lets_exactly_one_caller_rotate(tmp_path):
    """A refresh token is single-use even when two callers present it at once.

    The suite's shared in-memory SQLite connection cannot model two concurrent
    transactions, so this uses a file-backed database where each request gets
    its own connection. Losing the compare-and-set must be a 401, not a second
    live session minted from the same token.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'refresh-race.db'}",
        future=True,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            data = await _register(ac)

        async def attempt():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                return await ac.post(
                    "/api/v1/auth/refresh",
                    json={"refresh_token": data["refresh_token"]},
                )

        responses = await asyncio.gather(attempt(), attempt())

        assert sorted(r.status_code for r in responses) == [200, 401]

        async with session_factory() as session:
            rows = (await session.execute(select(RefreshToken))).scalars().all()

        assert len(rows) == 2
        assert sum(row.revoked_at is None for row in rows) == 1
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        await engine.dispose()


@pytest.mark.asyncio
async def test_refresh_rejects_an_expired_token(client, db_session):
    data = await _register(client)
    digest = hash_refresh_token(data["refresh_token"])
    row = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == digest)
        )
    ).scalar_one()
    row.expires_at = datetime.utcnow() - timedelta(days=1)
    await db_session.commit()

    res = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]}
    )
    assert res.status_code == 401


def test_refresh_tokens_are_opaque_and_stored_only_as_a_digest():
    token = generate_refresh_token()

    # Opaque: it is random bytes, not a JWT, so it claims nothing about its
    # holder and can be withdrawn the moment it is presented again.
    assert decode_access_token(token) is None
    digest = hash_refresh_token(token)
    assert len(digest) == 64
    assert token not in digest
    assert digest == hash_refresh_token(token)
    assert digest != hash_refresh_token(generate_refresh_token())


@pytest.mark.asyncio
async def test_no_plaintext_refresh_token_is_persisted(client, db_session):
    data = await _register(client)

    rows = (await db_session.execute(select(RefreshToken))).scalars().all()

    assert rows, "the session should have left a record behind"
    digests = {row.token_hash for row in rows}
    assert data["refresh_token"] not in digests
    assert hash_refresh_token(data["refresh_token"]) in digests


# ---------------------------------------------------------------------------
# B2: cookie transport and CSRF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_httponly_session_cookies(anonymous_client):
    data = await _register(anonymous_client)

    cookies = {c.name: c for c in anonymous_client.cookies.jar}
    assert cookies[ACCESS_COOKIE].value == data["access_token"]
    assert cookies[REFRESH_COOKIE].value == data["refresh_token"]
    # The whole point of B2: neither token is readable by page JavaScript.
    assert "HttpOnly" in cookies[ACCESS_COOKIE]._rest
    assert "HttpOnly" in cookies[REFRESH_COOKIE]._rest
    # The CSRF token is deliberately readable, because the page must echo it.
    assert CSRF_COOKIE in cookies
    assert "HttpOnly" not in cookies[CSRF_COOKIE]._rest
    assert cookies[ACCESS_COOKIE]._rest.get("SameSite", "").lower() == settings.AUTH_COOKIE_SAMESITE


@pytest.mark.asyncio
async def test_the_session_cookie_authenticates_without_a_header(anonymous_client):
    """The whole point of B2: no Authorization header is sent, and it still works.

    This is what lets the frontend stop reading a credential out of
    localStorage -- the browser attaches the cookie by itself.
    """
    await _register(anonymous_client)
    assert anonymous_client.cookies.get(ACCESS_COOKIE)

    res = await anonymous_client.get("/api/v1/auth/me")

    assert res.status_code == 200
    assert res.json()["email"]


@pytest.mark.asyncio
async def test_a_cookie_write_without_a_csrf_header_is_refused(anonymous_client):
    await _register(anonymous_client)
    token = anonymous_client.cookies.get(CSRF_COOKIE)
    assert token

    res = await anonymous_client.post(
        "/api/v1/subjects",
        json={"title": "Forged"},
        headers={"Cookie": f"{ACCESS_COOKIE}={anonymous_client.cookies.get(ACCESS_COOKIE)}"},
    )

    # A cross-origin page can cause the cookie to be sent but cannot read it, so
    # it cannot produce a matching header. That is the rejection.
    assert res.status_code == 403
    assert "CSRF" in res.json()["detail"]


@pytest.mark.asyncio
async def test_a_mismatched_csrf_header_is_refused(anonymous_client):
    await _register(anonymous_client)

    res = await anonymous_client.post(
        "/api/v1/subjects",
        json={"title": "Forged"},
        headers={CSRF_HEADER: "not-the-real-token"},
    )

    assert res.status_code == 403


@pytest.mark.asyncio
async def test_a_matching_csrf_header_is_accepted(anonymous_client):
    await _register(anonymous_client)

    res = await anonymous_client.post(
        "/api/v1/subjects", json={"title": "Legitimate"}
    )

    # The conftest client emulates a browser, so the header is present here.
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_a_bearer_request_is_not_subject_to_the_csrf_check(anonymous_client):
    """Swagger's OAuth2 flow, curl and mobile clients keep working unchanged.

    A header is supplied by the caller rather than attached by the browser, so
    there is no cross-site page riding on it.
    """
    data = await _register(anonymous_client)

    res = await anonymous_client.post(
        "/api/v1/subjects",
        json={"title": "From an API client"},
        headers=_auth(data["access_token"]),
    )

    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_a_request_with_no_session_is_not_subject_to_the_csrf_check(
    anonymous_client,
):
    """Login itself is a no-session request, so it must not need a CSRF token."""
    res = await anonymous_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"fresh-{uuid.uuid4().hex[:8]}@example.com",
            "full_name": "No Cookie Yet",
            "password": PASSWORD,
        },
    )

    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_safe_requests_do_not_need_a_csrf_header(anonymous_client):
    await _register(anonymous_client)

    res = await anonymous_client.get("/api/v1/auth/me")

    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_production_forces_secure_cookies(monkeypatch):
    """A cookie without Secure is sent over plaintext HTTP and captured on the way."""
    from app.core.config import Settings

    secure_in_dev = Settings(
        SECRET_KEY="k" * 40, ENVIRONMENT="development", AUTH_COOKIE_SECURE=False
    )
    assert secure_in_dev.AUTH_COOKIE_SECURE is False

    forced = Settings(SECRET_KEY="k" * 40, ENVIRONMENT="production")
    assert forced.AUTH_COOKIE_SECURE is True


def test_samesite_none_without_secure_is_rejected():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            SECRET_KEY="k" * 40,
            AUTH_COOKIE_SAMESITE="none",
            AUTH_COOKIE_SECURE=False,
        )


def test_samesite_none_with_secure_is_allowed():
    from app.core.config import Settings

    configured = Settings(
        SECRET_KEY="k" * 40,
        AUTH_COOKIE_SAMESITE="none",
        AUTH_COOKIE_SECURE=True,
    )

    assert configured.AUTH_COOKIE_SAMESITE == "none"
    assert configured.AUTH_COOKIE_SECURE is True


def test_samesite_none_is_allowed_in_production_because_secure_is_forced():
    from app.core.config import Settings

    configured = Settings(
        SECRET_KEY="k" * 40,
        ENVIRONMENT="production",
        AUTH_COOKIE_SAMESITE="none",
    )

    assert configured.AUTH_COOKIE_SAMESITE == "none"
    assert configured.AUTH_COOKIE_SECURE is True
