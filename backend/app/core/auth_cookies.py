"""B2: browser delivery of the auth tokens, and the CSRF check it obliges.

Before this, the frontend read its access token out of ``localStorage``, so any
script running on the origin -- a compromised dependency, an injected snippet, a
malicious extension with page access -- could read a credential that stayed
valid for a day. The tokens now ride in cookies that JavaScript cannot read, and
the trade-off is paid for explicitly:

* a cookie is attached by the browser to any request to this origin, including
  one an attacker site triggers, so cookie-authenticated writes need CSRF
  protection. That is what :func:`csrf_token_matches` is for, enforced in one
  place by :class:`app.main.CsrfProtectionMiddleware` rather than repeated on
  every route.
* ``SameSite`` is set from configuration, and ``Secure`` is forced on in
  production, so the cookie is not attached to cross-site requests and is never
  sent over plaintext HTTP.

Requests that authenticate with an ``Authorization`` header are unaffected: a
bearer token is not attached automatically by the browser, so there is nothing
for a cross-site page to ride on. That keeps Swagger's OAuth2 flow, curl and
mobile clients working unchanged.
"""

from typing import Optional

import hmac
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from app.core.config import settings

# Names are unprefixed to keep the existing `access_token` cookie name, which
# the frontend and any already-deployed client may already rely on.
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
# Deliberately readable by JavaScript: the double-submit pattern needs the page
# to echo this value into a request header. It is not a credential -- it is
# useless to an attacker who cannot read it, which is the whole mechanism.
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# RFC 9110: everything here can change state, so these are the methods that need
# the CSRF header.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def read_cookie(request: Request, name: str) -> Optional[str]:
    return request.cookies.get(name)


def csrf_token_matches(request: Request) -> bool:
    """True when the request carries a CSRF token the server can corroborate.

    Double submit: the cookie is set by this server, and the header must equal
    it. A cross-origin attacker can cause the browser to send the cookie but
    cannot read it, and cannot attach a custom header without first passing a
    CORS preflight this server will not approve.
    """
    cookie_value = request.cookies.get(CSRF_COOKIE)
    if not cookie_value:
        return False
    header_value = request.headers.get(CSRF_HEADER)
    if not header_value:
        return False
    # Constant-time compare: the two values are not a secret from each other in
    # the same way a password is, but there is no reason to leak a prefix length.
    return hmac.compare_digest(cookie_value, header_value)


def _base_cookie_kwargs() -> dict:
    return {
        # "/" because the API is mounted under /api/v1 but the session is not
        # scoped to any one of its routers.
        "path": "/",
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "domain": settings.AUTH_COOKIE_DOMAIN or None,
    }


def set_session_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    """Attaches the session cookies to a successful login/register response."""
    common = _base_cookie_kwargs()
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        **common,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        # Readable by design; see the module docstring.
        httponly=False,
        **common,
    )


def clear_session_cookies(response: Response) -> None:
    """Removes every session cookie.

    ``max_age=0`` with a matching path/domain is what actually deletes a
    cookie; a bare ``delete_cookie`` on a cookie that was set with different
    attributes can leave the original in place.
    """
    common = _base_cookie_kwargs()
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.set_cookie(name, "", max_age=0, httponly=True, **common)


def csrf_rejection_response() -> JSONResponse:
    # Generic, and deliberately a 403 rather than a 401: the request is not
    # asking for authentication, and a 401 would send cookie clients into a
    # refresh loop that will not help.
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "CSRF token missing or invalid."},
    )
