import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
import jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from app.core.config import settings

# B1: marker claim so this token can never be replayed as another token type.
ACCESS_TOKEN_TYPE = "access"
# 32 bytes of CSPRNG output, URL-safe encoded. Long enough that the stored
# digest cannot be searched, short enough to stay a cookie.
REFRESH_TOKEN_BYTES = 32

# Password Crypt Context using pbkdf2_sha256 (NIST recommended, secure, cross-platform)
#
# A6: `rounds` used to be left to the passlib default of 29,000, roughly 20x
# below current OWASP guidance for PBKDF2-HMAC-SHA256. It is now set explicitly
# from settings. The scheme is unchanged, and passlib carries the round count
# inside the hash string, so every hash written by the previous configuration
# still verifies -- `needs_rehash` below is what upgrades them on next login.
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
    pbkdf2_sha256__rounds=settings.PBKDF2_ROUNDS,
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a raw password against its hash.

    A stored value that is not a string, or that passlib cannot use as a hash
    (unknown algorithm, truncated/corrupt fields, unsupported scheme), is a
    failed verification rather than a server error. Only failures caused by the
    *stored hash* are swallowed here; unexpected errors still propagate so they
    are not silently reported as an ordinary bad password.
    """
    if not isinstance(hashed_password, (str, bytes)):
        # A NULL or otherwise damaged column value. Passlib would raise
        # TypeError here, but that is not a "wrong password" and should not be
        # confused with a programming error in the caller.
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except UnknownHashError:
        # The stored hash names an algorithm this context does not support.
        return False
    except ValueError:
        # A recognised scheme with malformed rounds, salt, or checksum.
        # passlib reports these as plain ValueError rather than a subclass.
        return False
    except OverflowError:
        # A stored rounds value at the top of passlib's accepted range can
        # overflow the underlying PBKDF2 backend before it computes anything.
        return False


def get_password_hash(password: str) -> str:
    """Generates a secure hash for a given password."""
    return pwd_context.hash(password)


def _rounds_in(hashed_password: str) -> Optional[int]:
    """Reads the iteration count out of a ``$pbkdf2-sha256$rounds$salt$sum`` hash."""
    parts = hashed_password.split("$")
    # ['', 'pbkdf2-sha256', rounds, salt, checksum]
    if len(parts) < 3 or parts[1] != "pbkdf2-sha256":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def needs_rehash(hashed_password: str) -> bool:
    """True when a stored hash uses a weaker work factor than is now configured.

    passlib 1.7.4 has no ``needs_rehash`` on this handler, so the round count is
    read from the hash itself. An unrecognised format returns False: rehashing
    is an upgrade path, and an unknown hash should be left alone for
    :func:`verify_password` to reject rather than silently rewritten.
    """
    rounds = _rounds_in(hashed_password)
    if rounds is None:
        return False
    return rounds < settings.PBKDF2_ROUNDS


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
) -> str:
    """Generates a JWT access token containing subject (user_id).

    B1: the payload carries the user's ``token_version`` as ``ver``. Every
    authenticated request compares the claim against the stored value, so bumping
    the column withdraws all outstanding access tokens at once -- which is what
    makes a password change able to end someone else's stolen session. ``typ``
    keeps this token from ever being accepted as something else later on.
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "iat": datetime.now(timezone.utc),
        "typ": ACCESS_TOKEN_TYPE,
        "ver": int(token_version),
    }
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decodes and validates a JWT token."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except jwt.PyJWTError:
        return None


def generate_refresh_token() -> str:
    """Creates the long-lived half of a session as an opaque random string.

    Deliberately not a JWT. An opaque value carries no claims to keep in sync
    and can be withdrawn the instant it is presented again, which is exactly what
    a signed-but-stateless token cannot do.
    """
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """Returns the digest stored in place of a refresh token.

    The plaintext is shown to the client exactly once. Only this digest is
    persisted, so a dump of the ``refresh_tokens`` table does not hand out live
    sessions -- the same reason a password is stored hashed. A plain SHA-256 is
    sufficient here and deliberately not a password hash: the input is 256 bits
    of CSPRNG output, so there is nothing to brute force.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
