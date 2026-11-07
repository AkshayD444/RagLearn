import uuid
from typing import AsyncGenerator, Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_cookies import ACCESS_COOKIE, read_cookie
from app.core.database import AsyncSessionLocal
from app.core.security import ACCESS_TOKEN_TYPE, decode_access_token
from app.models.subject import Subject
from app.models.user import User

# OAuth2 scheme pointing to login endpoint.
#
# `auto_error=False` is what lets a browser session work: with it on, FastAPI
# rejects the request before this function runs unless an Authorization header is
# present, which would make the HttpOnly cookie unusable. The security scheme is
# still declared, so Swagger's Authorize button and the OpenAPI document are
# unchanged.
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login", auto_error=False
)


def resolve_access_token(request: Request, header_token: Optional[str]) -> Optional[str]:
    """Picks the bearer token for a request: header first, then cookie.

    The header wins so that a caller who presents an explicit credential is
    always evaluated against it -- otherwise a stale cookie could shadow a
    deliberate header and, for instance, resolve to a different account.
    """
    if header_token:
        return header_token
    return read_cookie(request, ACCESS_COOKIE)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provides async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2),
) -> User:
    """Validates JWT token and resolves current authenticated User object."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = resolve_access_token(request, token)
    if not token:
        raise credentials_exception

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user_id_str: str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # B1: the token is only half the answer. A token that was signed by this
    # server, is unexpired, and names a live account may still have been
    # withdrawn -- by a logout, or by a password change that bumped
    # `token_version`. Comparing against the stored value is what makes those
    # actions take effect immediately instead of whenever the token happened to
    # expire.
    #
    # A payload with no `ver` claim predates this check and is treated as
    # withdrawn. Keeping it valid would leave exactly the 24-hour tokens this
    # change exists to shorten usable until their natural expiry; the cost is
    # that deploying this signs everyone out once.
    if payload.get("typ") != ACCESS_TOKEN_TYPE or payload.get("ver") != user.token_version:
        raise credentials_exception

    return user


async def require_owned_subject(
    db: AsyncSession,
    subject_id: uuid.UUID,
    current_user: User,
) -> Subject:
    """Verifies the subject exists, is not deleted, and belongs to the current user (404 otherwise)."""
    result = await db.execute(
        select(Subject).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found or access denied.",
        )
    return subject
