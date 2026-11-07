import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.auth_cookies import (
    REFRESH_COOKIE,
    clear_session_cookies,
    read_cookie,
    set_session_cookies,
)
from app.core.config import settings
from app.core.rate_limit import (
    LOGIN_POLICY,
    REGISTER_POLICY,
    email_identity,
    rate_limit,
)
from app.core.security import (
    create_access_token,
    get_password_hash,
    needs_rehash,
    verify_password,
)
from app.core.sessions import (
    find_active_session,
    issue_session,
    revoke_all_sessions,
    revoke_session,
    rotate_session,
)
from app.models.user import User
from app.schemas.user import (
    PasswordChange,
    RefreshRequest,
    Token,
    UserCreate,
    UserLogin,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Login is throttled per (client address, submitted email): the two halves
# together stop one host locking out every account, and stop a botnet
# concentrating on one account. See app/core/rate_limit.py.
limit_login = rate_limit(LOGIN_POLICY, identity=email_identity)
# Registration is throttled per client address only. Folding the submitted email
# into the key would give every new signup a fresh budget, which is exactly the
# free-account farming this limit exists to stop.
limit_register = rate_limit(REGISTER_POLICY)

# Length of the double-submit CSRF token handed to the browser alongside the
# session cookies. 32 bytes of CSPRNG, same reasoning as the refresh token.
CSRF_TOKEN_BYTES = 32


def _rehash_if_stale(user: User, plain_password: str) -> None:
    """Marker for the login handlers' lazy hash upgrade.

    A6: a password hashed with the old 29,000-round default still verifies
    (passlib reads the round count from the hash), so existing accounts are not
    locked out. What is missing is any way to move them onto the stronger work
    factor, so the upgrade happens where the plaintext is available: a
    successful login. The caller commits.
    """
    if needs_rehash(user.hashed_password):
        user.hashed_password = get_password_hash(plain_password)


async def _start_session(
    db: AsyncSession, user: User, response: Response
) -> Token:
    """Mints a full session and attaches its cookies.

    B1/B2: the access token is short-lived and carries the account's
    ``token_version``; the refresh token is opaque, stored only as a digest, and
    is what lets the user obtain a new access token after this one expires. Both
    are set as HttpOnly cookies, so the browser holds the session without the
    page's JavaScript ever seeing either value.

    The caller commits; the refresh row is staged on the same session so the
    cookie and the row cannot disagree.
    """
    refresh_token, _expires_at = await issue_session(db, user.id)
    access_token = create_access_token(
        subject=user.id, token_version=user.token_version
    )
    set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=secrets.token_urlsafe(CSRF_TOKEN_BYTES),
    )
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        token_type="bearer",
        user=user,
    )


def _insert_user_returning_row(db: AsyncSession, user: User) -> Any:
    """An ``INSERT`` that returns the stored row, or nothing if the email is taken.

    A7. Registration used to be a SELECT-then-INSERT: check for the email, add
    the row, and catch ``IntegrityError`` when a competing request had inserted
    the same email in between. That is a time-of-check/time-of-use race, and
    handling it by exception made the outcome depend on *when* the database
    noticed rather than on what was true when the row was written.

    ``ON CONFLICT DO NOTHING`` moves the decision into the database and into
    the same statement as the write. The unique index on ``users.email`` --
    which is not weakened by any of this -- decides the winner, and the loser
    inserts nothing and gets no row back. There is no exception to catch, no
    rollback that can discard a *sibling* request's committed work, and no
    second query whose answer could change between the insert and the reply.

    Two consequences worth stating. ``RETURNING`` populates the instance in the
    same statement, so the row never needs a follow-up ``refresh()``: the old
    ``await db.refresh(user)`` was racing the response, and a losing request's
    ``rollback()`` could expire or expunge the winner's instance out from under
    it, which is what raised "Could not refresh instance". And because a
    duplicate is now detected before anything is raised, the session is still
    usable afterwards rather than needing repair.

    The conflict target is named explicitly rather than left to bare
    ``ON CONFLICT DO NOTHING`` so it can only ever mean "this email is taken".
    An unrelated integrity failure -- a foreign key, a check constraint --
    still raises and still surfaces as a 500.

    ``ON CONFLICT`` is spelled per dialect rather than through
    ``sqlalchemy.insert``, which has no such clause: the generic builder would
    emit plain INSERT and silently stop deduplicating. The dialect is read from
    the live bind so one test engine (SQLite) and production (PostgreSQL) share
    this path.
    """
    dialect = db.get_bind().dialect.name
    if dialect.startswith("postgresql"):
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect.startswith("sqlite"):
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:  # pragma: no cover - the app ships on these two, both tested
        raise RuntimeError(
            f"registration needs an ON CONFLICT-capable INSERT to be race-safe, "
            f"but the {dialect!r} dialect has no such builder. Add one rather "
            f"than reverting to a check-then-insert."
        )

    return (
        dialect_insert(User)
        .values(
            email=user.email,
            full_name=user.full_name,
            hashed_password=user.hashed_password,
        )
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User)
    )


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    user_in: UserCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rate_limited: None = Depends(limit_register),
) -> Any:
    """Registers a new student user and starts their first session."""
    # A friendly fast path for the sequential duplicate, which is the common
    # case and costs one indexed lookup rather than a failed write. It is not
    # the safety net: it can miss a row that arrives a moment later, which is
    # exactly what the insert below is there to settle.
    existing_user = await db.execute(
        select(User.id).where(User.email == user_in.email.lower())
    )
    if existing_user.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists."
        )

    # Hashing is deliberately expensive, so it happens only once the fast path
    # above has declined to answer.
    user = User(
        email=user_in.email.lower(),
        full_name=user_in.full_name,
        hashed_password=get_password_hash(user_in.password)
    )

    # A7: this single statement is the atomic arbiter of the race.
    stored = (await db.execute(_insert_user_returning_row(db, user))).scalar_one_or_none()
    if stored is None:
        # A competing request took the email between the check and this insert.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists."
        )

    # The account and its first session are committed together, so a failure
    # here cannot leave a user row that no cookie can ever renew.
    token = await _start_session(db, stored, response)
    await db.commit()
    return token


@router.post("/login", response_model=Token)
async def login(
    login_data: UserLogin,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rate_limited: None = Depends(limit_login),
) -> Any:
    """Authenticates user with JSON credentials and returns a fresh session."""
    result = await db.execute(
        select(User).where(User.email == login_data.email.lower())
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _rehash_if_stale(user, login_data.password)
    # The refresh row is staged before the commit so a password upgrade and a
    # session insert land together or not at all.
    token = await _start_session(db, user, response)
    await db.commit()
    return token


@router.post("/login/form", response_model=Token)
async def login_form(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    _rate_limited: None = Depends(limit_login),
) -> Any:
    """Form-encoded login endpoint for Swagger UI OAuth2 support."""
    result = await db.execute(
        select(User).where(User.email == form_data.username.lower())
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _rehash_if_stale(user, form_data.password)
    token = await _start_session(db, user, response)
    await db.commit()
    return token


@router.post("/refresh", response_model=Token)
async def refresh(
    request: Request,
    response: Response,
    body: Optional[RefreshRequest] = None,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Exchanges a refresh token for a new access token (B1).

    The token may arrive in the body (for API clients) or in the HttpOnly cookie
    the browser was given. Both are accepted; the body is checked first so a
    caller passing an explicit token is never overridden by a stale cookie.
    """
    presented = (body.refresh_token if body else None) or read_cookie(
        request, REFRESH_COOKIE
    )
    session = await find_active_session(db, presented or "")
    if session is None:
        # Expired, revoked, or never issued -- indistinguishable on purpose, and
        # the response clears the cookies so the browser stops retrying with a
        # credential that cannot work.
        clear_session_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    if user is None:
        # The session outlived its account.
        clear_session_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Rotation: the presented token is spent, so a copy of it that turns up later
    # is rejected instead of quietly renewing the session a second time. The
    # claim is a compare-and-set, so two concurrent callers cannot both win.
    rotated = await rotate_session(db, session)
    if rotated is None:
        clear_session_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    new_refresh, _ = rotated
    access_token = create_access_token(
        subject=user.id, token_version=user.token_version
    )
    set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=new_refresh,
        csrf_token=secrets.token_urlsafe(CSRF_TOKEN_BYTES),
    )
    await db.commit()
    return Token(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        token_type="bearer",
        user=user,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout(
    request: Request,
    response: Response,
    body: Optional[RefreshRequest] = None,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Ends the current session (B1).

    This is the difference between signing out and forgetting a token: the
    refresh row is marked revoked, so the access token still in the browser stops
    being renewable and the long-lived credential is withdrawn. The short-lived
    access token is deliberately *not* invalidated here -- the point of it is to
    stay usable for one device's own in-flight requests. Signing out everywhere
    is a password change, which bumps ``token_version``.
    """
    presented = (body.refresh_token if body else None) or read_cookie(
        request, REFRESH_COOKIE
    )
    await revoke_session(db, presented or "")
    await db.commit()
    clear_session_cookies(response)
    return None


@router.post("/change-password", response_model=Token)
async def change_password(
    payload: PasswordChange,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Changes the password and invalidates every existing session (B1).

    Two writes do the invalidation, and both are needed. ``token_version`` is
    bumped, which is the only thing that can reach an access token the holder
    never presents again; every refresh row is revoked, so none of them can mint
    a replacement. The device making the change is then given a fresh session,
    so the student is not signed out of the browser they just used.
    """
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.token_version = (current_user.token_version or 0) + 1
    await revoke_all_sessions(db, current_user.id)
    token = await _start_session(db, current_user, response)
    await db.commit()
    return token


@router.get("/me", response_model=UserRead)
async def get_me(
    current_user: User = Depends(get_current_user)
) -> Any:
    """Fetches currently authenticated user's profile."""
    return current_user
