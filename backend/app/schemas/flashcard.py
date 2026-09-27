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


class FlashcardScheduleRead(BaseModel):
    """The card's position in the spaced-repetition schedule.

    Surfaced so the client can tell the student when a card will come back
    instead of presenting review as an endless loop. ``due_at`` is ``None``
    for a card that has never been studied, which means "due now" -- not
    "unknown".
    """

    ease_factor: float
    interval_days: int
    repetitions: int
    lapses: int
    due_at: Optional[datetime] = None
    last_reviewed_at: Optional[datetime] = None
    is_new: bool = False


class FlashcardRead(BaseModel):
    """A single card plus its derived review progress.

    ``review_count``/``correct_count`` are computed from the append-only review
    log at read time, so they are always consistent with the stored history.
    ``schedule`` is stored state, not a derivation.

    ``deck_id``/``deck_title``/``subject_id`` are carried on the card because a
    card outside its deck response is otherwise unaddressable: a cross-subject
    due queue has to group cards by deck, and the review endpoint takes a deck
    plus one result per card, so the client cannot reconstruct either grouping
    from the card alone.
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
    schedule: FlashcardScheduleRead
    deck_id: uuid.UUID
    deck_title: str
    subject_id: uuid.UUID


class FlashcardDeckRead(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    topic: Optional[str] = None
    card_count: int
    due_count: int = 0
    created_at: datetime
    cards: List[FlashcardRead] = Field(default_factory=list)


class FlashcardDeckSummary(BaseModel):
    """Deck metadata for the browse view, including aggregate review progress."""

    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    topic: Optional[str] = None
    card_count: int
    due_count: int = 0
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
    """Per-card result of a review, including progress and the new schedule.

    ``schedule`` is returned so the client can show "next review in 6 days"
    without re-fetching the card. The counts are post-write totals.
    """

    card_id: uuid.UUID
    is_correct: bool
    response: Optional[str] = None
    review_count: int
    correct_count: int
    schedule: FlashcardScheduleRead


class FlashcardReviewSummaryResponse(BaseModel):
    deck_id: uuid.UUID
    title: str
    reviewed_count: int
    correct_count: int
    incorrect_count: int
    percentage: float
    results: List[FlashcardReviewOutcome] = Field(default_factory=list)
    due_count: int = 0
    new_count: int = 0


class FlashcardDueResponse(BaseModel):
    """The review queue, plus the counts needed to render "Due today" honestly.

    ``due_count`` is the size of the whole queue, which can exceed
    ``len(cards)``; ``new_count`` is how much of that is never-studied material.
    Returning them alongside the page means the client can show "12 due" while
    displaying 20 without a second request.
    """

    cards: List[FlashcardRead] = Field(default_factory=list)
    due_count: int
    new_count: int


class FlashcardRescheduleResponse(BaseModel):
    """Confirmation that a card was returned to the due queue."""

    card_id: uuid.UUID
    schedule: FlashcardScheduleRead


class FlashcardTopicProgress(BaseModel):
    """Per-topic flashcard progress, annotated with read-only topic mastery.

    ``mastery_score``/``mastery_level`` are copied from the *quiz* evidence
    channel and are never written by the flashcard subsystem. Flashcard reviews
    accumulate in their own ``flashcard_*`` channel, reported separately as
    ``flashcard_mastery_score``, so self-graded reviews cannot inflate the quiz
    models that drive recommendations.
    """

    subject_id: uuid.UUID
    topic: str
    card_count: int
    reviewed_count: int
    correct_count: int
    accuracy: float
    mastery_score: Optional[float] = None
    mastery_level: Optional[str] = None
    flashcard_mastery_score: Optional[float] = None
    due_count: int = 0
