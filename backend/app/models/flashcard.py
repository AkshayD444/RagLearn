import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
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
    """

    __tablename__ = "flashcards"

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

    Reviews are append-only. Per-card progress is derived from these rows at read
    time rather than denormalized onto ``flashcards``, so a review can never
    race another review and lose an increment.
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
