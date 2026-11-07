import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator

# A5: `password` used to be a bare `str`, so a one-character password was
# accepted. The bounds live here, at the edge of the API, so the rule is
# enforced identically for the JSON and form paths and is reported as a 422
# with a field-level message.
MIN_PASSWORD_LENGTH = 8
# bcrypt silently truncates beyond 72 bytes. The app hashes with PBKDF2, which
# has no such limit, so this is a defensive cap rather than a compatibility
# requirement -- it keeps an unbounded request body from reaching the hasher.
MAX_PASSWORD_LENGTH = 128


class UserBase(BaseModel):
    email: EmailStr
    full_name: str


class UserCreate(UserBase):
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @field_validator("password")
    @classmethod
    def reject_blank_password(cls, v: str) -> str:
        """Rejects a password made only of whitespace.

        A minimum length of 8 is satisfied by eight spaces, which is not a
        password in any meaningful sense. No composition rules are imposed
        beyond this -- those push users toward predictable substitutions
        ("Passw0rd!") without adding real entropy.
        """
        if not v.strip():
            raise ValueError("Password must not be entirely whitespace.")
        return v


class UserLogin(BaseModel):
    # Deliberately *not* length-bounded. Login must accept whatever an account
    # was registered with, including passwords set before this policy existed,
    # and it must not reveal the registration policy to an unauthenticated
    # caller probing for valid accounts.
    email: EmailStr
    password: str


class PasswordChange(BaseModel):
    """B1: the security-sensitive action that ends every outstanding session."""

    # Not length-bounded, for the same reason UserLogin is not: the stored
    # password predates the current policy and must still be verifiable.
    current_password: str
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @field_validator("new_password")
    @classmethod
    def reject_blank_password(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Password must not be entirely whitespace.")
        return v


class UserRead(UserBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RefreshRequest(BaseModel):
    """B1: carries a refresh token for clients that cannot use a cookie jar.

    Every field is optional so the request body itself may be omitted entirely,
    which is what lets the browser flow send no body at all and rely on the
    HttpOnly cookie.
    """

    refresh_token: Optional[str] = None


class Token(BaseModel):
    # B1: `refresh_token` is new and optional so an older client that only reads
    # `access_token` keeps working. It is still returned in the body, not just
    # set as a cookie, because non-browser API clients (mobile, scripts) have
    # no cookie jar to hold it.
    access_token: str
    refresh_token: Optional[str] = None
    # Seconds until the access token expires, so a client can schedule a refresh
    # without hardcoding the server's configured lifetime.
    expires_in: Optional[int] = None
    token_type: str = "bearer"
    user: UserRead


class TokenPayload(BaseModel):
    sub: Optional[str] = None
    ver: Optional[int] = None
