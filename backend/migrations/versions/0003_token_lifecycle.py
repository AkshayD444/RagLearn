"""Server-side session state: users.token_version and the refresh_tokens table.

Revision ID: 0003_token_lifecycle
Revises: 0002_flashcard_scheduling
Create Date: 2026-09-29

Supports B1, which shortens the access token from 24 hours to 15 minutes and
needs somewhere to record that a credential has been withdrawn.

1. ``users`` gains ``token_version``, defaulting to 0. Every access token
   records the value it was minted with, so a mismatch is a 401. The server-side
   default is what makes this safe to run against a populated table: rows written
   by a concurrent request during a rolling deploy, or by anything not going
   through the ORM, land on a valid value instead of violating NOT NULL.
2. ``refresh_tokens`` stores one row per signed-in browser session, holding only
   the SHA-256 digest of the token. ``revoked_at IS NULL`` means live, so logout
   and password change withdraw a session by writing a timestamp rather than by
   deleting evidence that it ever existed.

Downgrade drops the table and the column. Tokens issued while this schema was in
place keep working against the old code, because that code does not read either
-- which is why this is safe to roll back and the 24-hour access tokens are not
revoked by it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_token_lifecycle"
down_revision: Union[str, None] = "0002_flashcard_scheduling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Hex SHA-256 is a fixed 64 characters, so this is not a truncation of
        # anything.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            # Deleting an account must not leave live sessions behind for a user
            # id that no longer resolves.
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Refresh looks a row up by digest, and cascade deletion filters by owner.
    # Both are on the hot path for every authenticated request.
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"),
        "refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(
        op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens"
    )
    op.drop_table("refresh_tokens")
    op.drop_column("users", "token_version")
