"""B1: server-side session lifecycle for the long-lived half of a login.

The access token is now short-lived and checked against ``users.token_version``
on every request (see :mod:`app.core.security` and
:func:`app.api.deps.get_current_user`). That covers "withdraw every session at
once", but it cannot express "log this one device out" -- and a student signing
in on a shared or library machine needs exactly that.

So the durable half of a session is an opaque refresh token recorded here. Three
properties matter:

* the plaintext is returned to the client once and never stored,
* every lookup is by digest, so the table holds no usable credential,
* a row is marked revoked rather than deleted, so a replayed token is
  recognisably withdrawn instead of looking like one that never existed.
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_refresh_token, hash_refresh_token
from app.models.refresh_token import RefreshToken


def _expiry(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.utcnow()) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


async def issue_session(
    db: AsyncSession, user_id, now: Optional[datetime] = None
) -> Tuple[str, datetime]:
    """Starts a session and returns ``(plaintext_refresh_token, expires_at)``.

    The caller is responsible for committing; these helpers only stage the work
    so that a route can add the cookie in the same request that created the row.
    """
    plaintext = generate_refresh_token()
    expires_at = _expiry(now)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(plaintext),
            expires_at=expires_at,
        )
    )
    return plaintext, expires_at


async def find_active_session(
    db: AsyncSession, plaintext: str, now: Optional[datetime] = None
) -> Optional[RefreshToken]:
    """Returns the live row for ``plaintext``, or ``None``.

    Expiry and revocation are both filtered in SQL rather than in Python so the
    check cannot be skipped by a code path that forgets it. The digest is used
    as the lookup key directly; there is no column holding the plaintext.
    """
    if not plaintext:
        return None
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(plaintext),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > (now or datetime.utcnow()),
        )
    )
    return result.scalar_one_or_none()


async def rotate_session(
    db: AsyncSession, existing: RefreshToken, now: Optional[datetime] = None
) -> Optional[Tuple[str, datetime]]:
    """Exchanges a live refresh token for a new one, or returns ``None``.

    Refusing to reissue the same value is what makes a stolen refresh token
    detectable: the legitimate client and the thief race to present it, the first
    one wins and rotates it, and the second is rejected as already-revoked
    instead of quietly getting a second week of access.

    The claim is a compare-and-set rather than a read followed by a write. Two
    requests can both call :func:`find_active_session` before either commits;
    an unconditional assignment would let both rotate the same row and both
    receive a new token. Only the update that still sees ``revoked_at IS NULL``
    wins, so the loser inserts nothing.
    """
    result = await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.id == existing.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now or datetime.utcnow())
        .returning(RefreshToken.id)
        .execution_options(synchronize_session=False)
    )
    if result.scalar_one_or_none() is None:
        return None
    return await issue_session(db, existing.user_id, now=now)


async def revoke_session(db: AsyncSession, plaintext: str) -> bool:
    """Revokes one session. Returns whether a live session was actually ended."""
    row = await find_active_session(db, plaintext)
    if row is None:
        return False
    row.revoked_at = datetime.utcnow()
    return True


async def revoke_all_sessions(
    db: AsyncSession, user_id, now: Optional[datetime] = None
) -> int:
    """Revokes every live session for an account.

    Used by the password-change path, alongside the ``token_version`` bump: the
    bump kills the short-lived access tokens, and this kills the refresh tokens
    that could otherwise mint new ones.
    """
    stamp = now or datetime.utcnow()
    result = await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=stamp)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
