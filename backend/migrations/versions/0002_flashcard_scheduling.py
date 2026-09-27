"""Flashcard spaced repetition: scheduling columns, evidence channel, plan counts.

Revision ID: 0002_flashcard_scheduling
Revises: 0001_initial_schema
Create Date: 2026-09-27

Three changes, all additive and all backfilled from column defaults so the
migration is safe to run against a populated database with no data movement:

1. ``flashcards`` gains SM-2 scheduling state. Every existing card lands in the
   "never reviewed" state (``due_at IS NULL``), which the scheduler already
   treats as due immediately. No card is lost or double-counted.
2. ``topic_mastery`` gains a separate flashcard evidence channel, so a card
   review is recorded without inflating the quiz-derived mastery score.
3. ``study_plan_items`` gains ``due_cards`` so a plan can report how much
   scheduled review a topic actually has waiting.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_flashcard_scheduling"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------- flashcard scheduling
    # Defaults are server-side so a row inserted outside the ORM -- including
    # a concurrent request during a rolling deploy -- still lands in a valid
    # "never reviewed" state instead of violating NOT NULL.
    op.add_column(
        "flashcards",
        sa.Column(
            "ease_factor", sa.Float(), nullable=False, server_default=sa.text("2.5")
        ),
    )
    op.add_column(
        "flashcards",
        sa.Column(
            "interval_days", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "flashcards",
        sa.Column(
            "repetitions", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "flashcards", sa.Column("lapses", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.add_column(
        "flashcards", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "flashcards",
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Two indexes, because the due queue is queried two different ways.
    # `ix_flashcards_due_at` serves the cross-subject dashboard count; the
    # composite serves "which cards in this deck are due", which is always
    # filtered on deck_id first.
    op.create_index("ix_flashcards_due_at", "flashcards", ["due_at"])
    op.create_index(
        "ix_flashcards_deck_due_at", "flashcards", ["deck_id", "due_at"]
    )

    # ------------------------------------------------- flashcard evidence channel
    op.add_column(
        "topic_mastery",
        sa.Column(
            "flashcard_reviews", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "topic_mastery",
        sa.Column(
            "flashcard_correct", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "topic_mastery",
        sa.Column(
            "flashcard_accuracy", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "topic_mastery",
        sa.Column(
            "flashcard_mastery", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
    )

    # ------------------------------------------------------ study plan due counts
    op.add_column(
        "study_plan_items",
        sa.Column("due_cards", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("study_plan_items", "due_cards")

    op.drop_column("topic_mastery", "flashcard_mastery")
    op.drop_column("topic_mastery", "flashcard_accuracy")
    op.drop_column("topic_mastery", "flashcard_correct")
    op.drop_column("topic_mastery", "flashcard_reviews")

    op.drop_index("ix_flashcards_deck_due_at", table_name="flashcards")
    op.drop_index("ix_flashcards_due_at", table_name="flashcards")
    op.drop_column("flashcards", "last_reviewed_at")
    op.drop_column("flashcards", "due_at")
    op.drop_column("flashcards", "lapses")
    op.drop_column("flashcards", "repetitions")
    op.drop_column("flashcards", "interval_days")
    op.drop_column("flashcards", "ease_factor")
