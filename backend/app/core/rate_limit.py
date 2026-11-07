"""Sliding-window rate limiting.

Storage: in-process, and that is a stated constraint rather than a hidden one
===========================================================================

The counters live in this process's memory. Two consequences, both of which the
code now surfaces instead of leaving to be discovered:

1. **The limit is per process, not global.** Running N uvicorn workers gives a
   single caller N x the configured budget, because each worker counts its own
   requests and none of them can see the others'. The project ships as a single
   worker, where the configured limits are exact.
   ``RATE_LIMIT_WORKER_COUNT`` declares the worker count and
   :func:`describe_effective_limit_scope` logs the effective per-caller limit at
   startup, at WARNING when the declared count is above one. There is no
   portable way to detect the worker count from inside the process, which is why
   it is declared rather than discovered.

   A global limit across workers needs a shared counter store. Redis is the
   obvious one and is not currently a dependency: adding a service to the
   project's runtime in order to keep a deployment shape the app does not use
   would be a worse trade than stating the limitation. The seam for it is
   :class:`RateLimitBackend` -- implement ``hit``/``retry_after``/``reset``
   against a shared store and it drops in behind the same
   :func:`get_backend` factory, with no change to the policies below or to the
   :func:`rate_limit` dependency.

2. **Counters do not survive a restart.** That direction fails closed, which is
   the safe one: a deploy briefly gives every caller a fresh budget.

Why this is hand-rolled rather than a dependency
------------------------------------------------
A sliding window is used rather than a fixed one that resets on the boundary,
because the latter hands out a full second budget to anyone who straddles it.
Implementing it directly keeps the project free of a dependency and a storage
backend for a counter a dict can hold, and -- because the clock is injectable --
its tests are deterministic instead of sleep-based.

Per-policy isolation
--------------------
Policies have different windows (login and generative: 60s; register: 300s;
upload: 3600s) and share one dictionary, so each bucket records the window it
was created with and is expired against *its own* window. Expiring every bucket
against whichever policy happened to be touched last is what a single shared
``window`` argument invites, and it silently hands out a fresh upload budget
every time someone logs in.
"""

import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Deque, Optional, Protocol, runtime_checkable

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("app.rate_limit")

# Bounds the number of tracked keys. Without it, an attacker rotating client
# addresses grows the dict without limit. Eviction is least-recently-used, and
# only triggers once the cap is reached.
MAX_TRACKED_KEYS = 10_000


@runtime_checkable
class RateLimitBackend(Protocol):
    """The three operations a rate-limit store has to provide.

    Exists so the storage can be replaced without touching the policies or the
    FastAPI dependency. :class:`SlidingWindowLimiter` is the in-process
    implementation; a Redis-backed class satisfying this Protocol is the whole
    of what a shared counter store would require.

    ``hit`` must be atomic across processes, not merely thread-safe: that
    property is the entire difference between a real global limit and N
    independent per-worker ones.
    """

    def hit(self, key: str, limit: int, window: float, now: Optional[float] = None) -> bool:
        """Records a request against ``key``; returns False when over budget."""

    def retry_after(self, key: str, window: float, now: Optional[float] = None) -> int:
        """Seconds until the oldest hit in ``key``'s window expires (>= 1)."""

    def reset(self) -> None:
        """Clears all counters. Used between tests."""


class _Bucket:
    """One tracked key: its hit timestamps and the window they count against.

    ``window`` is stored rather than passed in at expiry time because a key is
    created by one policy and must be judged by that policy's rules forever
    after, even while other policies are being written to the same dict.
    """

    __slots__ = ("hits", "window")

    def __init__(self, window: float) -> None:
        self.hits: Deque[float] = deque()
        self.window = float(window)

    def drop_expired(self, now: float) -> bool:
        """Trims timestamps older than this bucket's own window.

        Returns whether the bucket is now empty and can be discarded. A blocked
        request is not recorded, so a bucket whose hits have all aged out is
        genuinely empty and safe to delete.
        """
        cutoff = now - self.window
        while self.hits and self.hits[0] <= cutoff:
            self.hits.popleft()
        return not self.hits

    def retry_after(self, now: float) -> int:
        if not self.hits:
            return 1
        return max(1, int(self.window - (now - self.hits[0])) + 1)


class SlidingWindowLimiter:
    """Thread-safe fixed-memory sliding-window counter.

    A sliding window (rather than a fixed one that resets on the boundary) is
    used because the latter lets a caller send the full budget at the end of
    one window and again at the start of the next.

    Thread safety comes from the single lock around the whole read-modify-write;
    the ``hit`` method is intentionally one critical section, since splitting the
    count from the append would let two threads both observe room in the budget.
    """

    def __init__(self, max_keys: int = MAX_TRACKED_KEYS) -> None:
        self._hits: "OrderedDict[str, _Bucket]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def _prune(self, now: float) -> None:
        """Enforces the key cap, then drops buckets that are individually spent.

        Called with no window on purpose: expiry is each bucket's own decision
        (:meth:`_Bucket.drop_expired`), so a 60s policy can never expire a 3600s
        bucket. Sweeping a shared ``now - window`` cutoff here is what allowed a
        single login request to reset the upload budget.

        Expiry only runs once the cap is reached. Below it, leaving an idle key
        in the dict costs one small object and saves a full sweep on every
        request, which is the right trade when the cap is 10,000.
        """
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)

        for key in [k for k, bucket in self._hits.items() if bucket.drop_expired(now)]:
            del self._hits[key]

    def hit(self, key: str, limit: int, window: float, now: Optional[float] = None) -> bool:
        """Records a request against ``key``; returns False when over budget."""
        now = time.monotonic() if now is None else now

        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = _Bucket(window)
                self._hits[key] = bucket
            else:
                self._hits.move_to_end(key)
                # Expire this bucket against its own window before counting, so
                # a caller whose budget has aged out is not rejected by hits that
                # have already left the window.
                bucket.drop_expired(now)

            allowed = len(bucket.hits) < limit
            if allowed:
                # Only an allowed request is recorded. Appending on rejection
                # would let a client under a fixed budget keep pushing its own
                # expiry out indefinitely.
                bucket.hits.append(now)
            self._prune(now)
            return allowed

    def retry_after(self, key: str, window: float, now: Optional[float] = None) -> int:
        """Seconds until the oldest hit in the window expires (>= 1).

        ``window`` is accepted for interface compatibility with a shared
        backend, which needs it to rebuild an expiry; the in-process store reads
        the window off the bucket itself.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                return 1
            return bucket.retry_after(now)

    def reset(self) -> None:
        """Clears all counters. Used between tests."""
        with self._lock:
            self._hits.clear()

    def tracked_key_count(self) -> int:
        """Number of live buckets. Read by the bounds test."""
        with self._lock:
            return len(self._hits)


#: The process-wide store the dependency below consults. A module global rather
#: than a per-call construction so every policy shares one set of counters.
limiter = SlidingWindowLimiter()


def get_backend() -> RateLimitBackend:
    """The store the :func:`rate_limit` dependency reads and writes.

    The one place a shared backend would be selected. Returning the in-process
    limiter keeps the project runnable locally with no extra services; a
    Redis-backed implementation of :class:`RateLimitBackend` selected here is
    the only change a global multi-worker limit requires.
    """
    return limiter


class RateLimitPolicy:
    """A named limit: how many requests, over how long, and what it is for."""

    __slots__ = ("name", "limit", "window", "key_by_user")

    def __init__(self, name: str, limit: int, window: int, key_by_user: bool) -> None:
        self.name = name
        self.limit = limit
        self.window = window
        # Authenticated routes are keyed by user id so that several people
        # behind one NAT do not consume each other's budget. Unauthenticated
        # routes have no user, so they fall back to the client address.
        self.key_by_user = key_by_user

    def window_seconds(self) -> int:
        return self.window

    def limit_size(self) -> int:
        return self.limit


LOGIN_POLICY = RateLimitPolicy(
    "login",
    settings.RATE_LIMIT_LOGIN_ATTEMPTS,
    settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS,
    key_by_user=False,
)
REGISTER_POLICY = RateLimitPolicy(
    "register",
    settings.RATE_LIMIT_REGISTER_ATTEMPTS,
    settings.RATE_LIMIT_REGISTER_WINDOW_SECONDS,
    key_by_user=False,
)
GENERATIVE_POLICY = RateLimitPolicy(
    "generative",
    settings.RATE_LIMIT_GENERATIVE_ATTEMPTS,
    settings.RATE_LIMIT_GENERATIVE_WINDOW_SECONDS,
    key_by_user=True,
)
UPLOAD_POLICY = RateLimitPolicy(
    "upload",
    settings.RATE_LIMIT_UPLOAD_ATTEMPTS,
    settings.RATE_LIMIT_UPLOAD_WINDOW_SECONDS,
    key_by_user=True,
)


def client_address(request: Request) -> str:
    """Best-effort client address.

    ``request.client.host`` is the peer address as seen by the app. Uvicorn's
    proxy headers are not trusted by default, and trusting a client-supplied
    ``X-Forwarded-For`` without a proxy in front would make the limiter
    trivially bypassable, so only the transport peer is used.
    """
    client = request.client
    return client.host if client and client.host else "unknown"


async def email_identity(request: Request) -> str:
    """Bucket key for per-account throttling: client address plus submitted email.

    Both halves matter. Keying on the email alone lets one attacker lock every
    account out from a single host; keying on the address alone misses
    distributed credential stuffing against one account. A missing email
    collapses to the empty string, which pools all malformed attempts into one
    bucket -- the right outcome, since those are not aimed at a real account.

    Reading the body here is safe: Starlette memoises it on the ``Request``, and
    FastAPI parses the same instance afterwards, so the body is only ever read
    from the wire once. Covering both JSON and form encoding matters because
    ``/auth/login`` and ``/auth/login/form`` share one policy.
    """
    email = getattr(request.state, "rate_limit_identity", None)
    if email is None:
        email = await _extract_email(request)
        request.state.rate_limit_identity = email
    return f"{client_address(request)}:{str(email).lower()}"


async def _extract_email(request: Request) -> str:
    try:
        content_type = (request.headers.get("content-type") or "").lower()
        if "form" in content_type or "multipart" in content_type:
            form = await request.form()
            return str(form.get("username") or form.get("email") or "")
        if "json" in content_type:
            payload = await request.json()
            if isinstance(payload, dict):
                return str(payload.get("email") or "")
    except Exception:
        # A body we cannot parse is exactly the traffic a limiter should still
        # count. Fall through to the pooled empty-email bucket.
        logger.debug("rate_limit_identity_unreadable", exc_info=True)
    return ""


def _too_many_requests(policy: RateLimitPolicy, key: str) -> HTTPException:
    retry_after = get_backend().retry_after(key, policy.window_seconds())
    logger.warning(
        "rate_limit_exceeded",
        extra={"policy": policy.name, "retry_after_seconds": retry_after},
    )
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "Too many requests. Please wait before trying this action again."
        ),
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Policy": policy.name,
        },
    )


async def _enforce(
    policy: RateLimitPolicy,
    request: Request,
    identity,
    authenticated_user_id: Optional[str],
) -> None:
    """The whole check, shared by both dependency shapes below.

    Split out so the user-keyed and address-keyed dependencies cannot drift
    apart: they differ only in where the key comes from, not in what they do
    with it.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    key = await _resolve_key(policy, request, identity, authenticated_user_id)
    bucket = f"{policy.name}:{key}"
    backend = get_backend()
    if not backend.hit(bucket, policy.limit_size(), policy.window_seconds()):
        raise _too_many_requests(policy, bucket)


def rate_limit(
    policy: RateLimitPolicy,
    identity: Optional[Callable[[Request], str]] = None,
    user_dependency: Optional[Callable] = None,
) -> Callable:
    """Builds a FastAPI dependency enforcing ``policy``.

    ``identity`` resolves the bucket key from the request and may be async (it
    reads the submitted email). Routes that throttle a *specific account* pass
    such a callable; routes that throttle a *caller* leave it unset.

    ``user_dependency`` is the authenticated-user resolver for a user-scoped
    policy, and it is **mandatory** when ``policy.key_by_user`` is true.

    Why mandatory, and why a sub-dependency
    ---------------------------------------
    A user-scoped policy exists to stop one account from spending a shared
    budget. Falling back to the client address when the user id is unavailable
    silently destroys that: everyone behind one NAT pools into a single bucket
    and unrelated people throttle each other, which is the exact failure
    ``key_by_user`` was introduced to prevent. So a user-scoped policy with no
    resolver is rejected when the dependency is built -- at import time, once --
    rather than quietly producing address-keyed limits.

    Declaring the resolver as ``Depends(...)`` *of the limiter* is what makes the
    ordering explicit. The alternative, having the limiter read an id that
    ``get_current_user`` stashed on ``request.state``, only works if the auth
    dependency happens to run first -- which sibling route parameters do not
    guarantee. As a sub-dependency, FastAPI's own solver establishes the
    order, and because the callable is the same one the route already declares,
    the solver caches it: the token is decoded and the user loaded once, never
    twice.

    The resolver is injected rather than imported so this module stays below
    ``app.api`` in the dependency graph. Importing ``get_current_user`` here
    would invert the layering and risk a cycle the first time an auth module
    wanted the limiter.

    The bucket key is namespaced by policy name, so the four policies can never
    read or spend each other's counters even though they share one dict. The
    namespace is not sufficient on its own -- the store also has to expire each
    bucket against its own window, which is why :class:`_Bucket` carries one.

    The limiter is a no-op when ``RATE_LIMIT_ENABLED`` is false, so a developer
    can turn it off in ``.env`` without editing code. Note that this is a
    deliberate opt-out for local development, and a deployment that ships with
    it on has no abuse control on login, registration or the generative
    endpoints at all.

    The store is resolved per request through :func:`get_backend` rather than
    captured, so swapping in a shared backend takes effect without touching this
    function.
    """
    if policy.key_by_user:
        if user_dependency is None:
            raise ValueError(
                f"Rate limit policy {policy.name!r} is user-scoped "
                f"(key_by_user=True), so rate_limit() requires a "
                f"user_dependency. Without one the bucket would be keyed by "
                f"client address, pooling every account behind a shared NAT into "
                f"one budget. Pass user_dependency=get_current_user, or set "
                f"key_by_user=False for a policy that genuinely means per-IP."
            )
        if identity is not None:
            raise ValueError(
                f"Rate limit policy {policy.name!r} is user-scoped, so it is "
                f"keyed by the authenticated user id. Passing identity as well "
                f"would give two conflicting keys for one request; drop one."
            )

        async def dependency(
            request: Request,
            authenticated_user=Depends(user_dependency),
        ) -> None:
            # `authenticated_user` is the resolved User, delivered by the
            # declared sub-dependency. Reading the id off the object rather
            # than off request.state is what makes the key independent of
            # whether some other dependency happened to have run.
            await _enforce(policy, request, identity, str(authenticated_user.id))

    else:

        async def dependency(request: Request) -> None:
            await _enforce(policy, request, identity, None)

    return dependency


async def _resolve_key(
    policy: RateLimitPolicy,
    request: Request,
    identity,
    authenticated_user_id: Optional[str],
) -> str:
    """Resolves the bucket key, awaiting the identity callable if it is async.

    ``email_identity`` has to read the request body, so it is a coroutine; the
    other branches are cheap attribute reads and stay synchronous.
    """
    if identity is not None:
        return await identity(request)
    if policy.key_by_user:
        # Non-None by construction: rate_limit() refuses a user-scoped policy
        # without a resolver, so there is no path where this is a silent None.
        return authenticated_user_id
    return client_address(request)


def describe_effective_limit_scope() -> None:
    """Logs the scope of the configured limits, loudly if it is not global.

    Called from the application lifespan. With the declared single worker the
    configured numbers are what a caller actually gets and this is informational;
    above one worker each caller can spend that much per worker, which is
    logged at WARNING because it materially weakens the abuse control on login,
    registration and the generative endpoints.

    Deliberately read-only. Reporting the real effective budget is the whole
    point; quietly scaling the limits up to match the worker count would hide
    the same problem this function exists to surface.
    """
    workers = max(1, int(settings.RATE_LIMIT_WORKER_COUNT))
    if settings.RATE_LIMIT_ENABLED and workers > 1:
        logger.warning(
            "rate_limit_scope_is_per_process",
            extra={
                "declared_worker_count": workers,
                "login_effective_attempts": LOGIN_POLICY.limit * workers,
                "register_effective_attempts": REGISTER_POLICY.limit * workers,
                "generative_effective_attempts": GENERATIVE_POLICY.limit * workers,
                "upload_effective_attempts": UPLOAD_POLICY.limit * workers,
                "detail": (
                    "Counters are in-process, so each caller can spend the "
                    "configured budget once per worker. A global limit needs a "
                    "shared store (see RateLimitBackend in app/core/rate_limit.py)."
                ),
            },
        )
        return

    logger.info(
        "rate_limit_scope",
        extra={
            "declared_worker_count": workers,
            "storage": "in-process",
            "detail": "Configured limits are the effective per-caller limits.",
        },
    )


__all__ = [
    "GENERATIVE_POLICY",
    "LOGIN_POLICY",
    "REGISTER_POLICY",
    "UPLOAD_POLICY",
    "RateLimitBackend",
    "RateLimitPolicy",
    "SlidingWindowLimiter",
    "client_address",
    "describe_effective_limit_scope",
    "email_identity",
    "get_backend",
    "limiter",
    "rate_limit",
]
