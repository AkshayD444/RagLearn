import uuid
from datetime import datetime
from typing import List, TYPE_CHECKING
from sqlalchemy import Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.refresh_token import RefreshToken
    from app.models.subject import Subject


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    # B1: the account-wide "withdraw everything" switch. Every access token
    # records the value it was minted with, and a mismatch is a 401. Incrementing
    # it ends all outstanding sessions for this account in one write, which is
    # what a password change needs -- a per-session row cannot reach tokens the
    # user never presents again.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Relationships
    subjects: Mapped[List["Subject"]] = relationship(
        "Subject", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[List["RefreshToken"]] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )
