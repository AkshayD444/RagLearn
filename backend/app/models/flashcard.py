import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class FlashcardDeck(Base):
    """A generated set of flashcards grounded in one subject's indexed material."""

    __tablename__ = "flashcard_decks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    card_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    cards: Mapped[List["Flashcard"]] = relationship(
        "Flashcard",
        back_populates="deck",
        cascade="all, delete-orphan",
        order_by="Flashcard.position",
    )


class Flashcard(Base):
    """A single question/answer pair inside a deck.

    The citation columns mirror ``QuizQuestion``: they are snapshots, so a card
    keeps its provenance even after the source document and chunk are deleted.

    The ``ease_factor`` / ``interval_days`` / ``repetitions`` / ``lapses`` /
    ``due_at`` block is spaced-repetition state, owned by
    :mod:`app.services.spaced_repetition`. It is denormalized onto the card
    rather than derived from the review log because the due queue has to be an
    indexed range scan: "which cards are due" is not answerable by aggregation
    over an append-only log without a full scan on every request.

    Two indexes back the queue. They are declared here and not only in the
    migration so a brand-new database (created by the bootstrap path) is
    indexed identically to one that ran the migrations -- otherwise a fresh
    install would silently lose the deck-scoped index. ``due_at`` alone serves
    the cross-subject dashboard; the composite serves the deck-scoped review,
    which always filters on ``deck_id`` first.
    """

    __tablename__ = "flashcards"
    __table_args__ = (Index("ix_flashcards_deck_due_at", "deck_id", "due_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deck_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flashcard_decks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    front_text: Mapped[str] = mapped_column(Text, nullable=False)
    back_text: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Citation metadata (snapshots survive document deletion; FKs null out)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Spaced-repetition state. Defaults describe a card that has never been
    # reviewed: `due_at IS NULL` is the "new" marker, not a missing value.
    ease_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    deck: Mapped["FlashcardDeck"] = relationship(
        "FlashcardDeck", back_populates="cards"
    )
    reviews: Mapped[List["FlashcardReview"]] = relationship(
        "FlashcardReview", back_populates="card", cascade="all, delete-orphan"
    )


class FlashcardReview(Base):
    """One self-graded review of a single flashcard.

    Reviews are append-only and remain the audit trail. Per-card *counts*
    (reviewed / correct) are still derived from these rows at read time, so a
    review can never race another review and lose an increment.

    The scheduler's mutable state lives on :class:`Flashcard` instead: it is a
    fold over the whole history, so recomputing it per read is not an option.
    That split is deliberate -- counts are order-independent, the schedule is not.
    """

    __tablename__ = "flashcard_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flashcards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deck_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flashcard_decks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    card: Mapped["Flashcard"] = relationship("Flashcard", back_populates="reviews")
