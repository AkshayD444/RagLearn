import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(Base):
    """B1: one row per signed-in browser session.

    A logout, or a password change, writes here rather than trying to remember
    which bearer tokens are still in the wild. Only the SHA-256 digest of the
    token is stored, so this table cannot be read back into live credentials.

    Rows are kept after revocation rather than deleted: the point of
    ``revoked_at`` is that a replayed token is recognisably revoked, instead of
    looking like a token that was never issued.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Hex SHA-256: a fixed 64 characters, and the lookup is indexed so refresh
    # does not scan the table.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Null means "live". A rotation marks the outgoing row and writes a new one.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")
