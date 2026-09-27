import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class FlashcardGenerateRequest(BaseModel):
    """Flashcard generation configuration scoped to a single user-owned subject."""

    subject_id: uuid.UUID
    card_count: int = Field(default=10, ge=1, le=40)
    topic: Optional[str] = Field(default=None, max_length=300)

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class FlashcardRead(BaseModel):
    """A single card plus its derived review progress.

    ``review_count``/``correct_count`` are computed from the append-only review
    log at read time, so they are always consistent with the stored history.
    """

    id: uuid.UUID
    front_text: str
    back_text: str
    hint: Optional[str] = None
    topic: Optional[str] = None
    position: int
    source_document_id: Optional[uuid.UUID] = None
    source_document_title: Optional[str] = None
    source_chunk_id: Optional[uuid.UUID] = None
    source_page_number: Optional[int] = None
    source_snippet: Optional[str] = None
    review_count: int = 0
    correct_count: int = 0


class FlashcardDeckRead(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    topic: Optional[str] = None
    card_count: int
    created_at: datetime
    cards: List[FlashcardRead] = Field(default_factory=list)


class FlashcardDeckSummary(BaseModel):
    """Deck metadata for the browse view, including aggregate review progress."""

    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    topic: Optional[str] = None
    card_count: int
    created_at: datetime
    reviewed_card_count: int = 0
    correct_count: int = 0


class FlashcardReviewItem(BaseModel):
    """A single self-graded review of one card."""

    card_id: uuid.UUID
    is_correct: bool
    response: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("response")
    @classmethod
    def normalize_response(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class FlashcardReviewRequest(BaseModel):
    reviews: List[FlashcardReviewItem] = Field(min_length=1)


class FlashcardReviewOutcome(BaseModel):
    """Per-card result of a review, including progress after the write."""

    card_id: uuid.UUID
    is_correct: bool
    response: Optional[str] = None
    review_count: int
    correct_count: int


class FlashcardReviewSummaryResponse(BaseModel):
    deck_id: uuid.UUID
    title: str
    reviewed_count: int
    correct_count: int
    incorrect_count: int
    percentage: float
    results: List[FlashcardReviewOutcome] = Field(default_factory=list)


class FlashcardTopicProgress(BaseModel):
    """Per-topic flashcard progress, annotated with read-only topic mastery.

    ``mastery_score``/``mastery_level`` are copied from ``topic_mastery`` and are
    never written by the flashcard subsystem: quiz evidence remains the only
    input to the Phase 6/7 mastery and recommendation models.
    """

    subject_id: uuid.UUID
    topic: str
    card_count: int
    reviewed_count: int
    correct_count: int
    accuracy: float
    mastery_score: Optional[float] = None
    mastery_level: Optional[str] = None
