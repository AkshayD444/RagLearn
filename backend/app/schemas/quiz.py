import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuizDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuizGenerateRequest(BaseModel):
    """Quiz generation configuration scoped to a single user-owned subject."""
    subject_id: uuid.UUID
    question_count: int = Field(default=5, ge=1, le=20)
    difficulty: QuizDifficulty = QuizDifficulty.MEDIUM
    topic: Optional[str] = Field(default=None, max_length=300)

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class QuizQuestionRead(BaseModel):
    """Pre-submission question. The correct answer and explanation are hidden
    until the quiz is submitted (evaluation reveals them)."""
    id: uuid.UUID
    question_text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    difficulty: str
    source_document_id: Optional[uuid.UUID] = None
    source_document_title: Optional[str] = None
    source_chunk_id: Optional[uuid.UUID] = None
    source_page_number: Optional[int] = None
    source_snippet: Optional[str] = None


class QuizRead(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    title: str
    difficulty: str
    question_count: int
    topic: Optional[str] = None
    created_at: datetime
    questions: List[QuizQuestionRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AnswerItem(BaseModel):
    question_id: uuid.UUID
    selected_option: str = Field(min_length=1, max_length=8)
    time_taken: Optional[float] = Field(default=None, ge=0)

    @field_validator("selected_option")
    @classmethod
    def validate_option(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"A", "B", "C", "D"}:
            raise ValueError("selected_option must be one of A, B, C, or D.")
        return value


class QuizSubmission(BaseModel):
    answers: List[AnswerItem] = Field(min_length=1)
    started_at: Optional[datetime] = None


class QuestionResult(BaseModel):
    question_id: uuid.UUID
    question_text: str
    selected_option: str
    correct_option: str
    is_correct: bool
    explanation: str
    difficulty: str
    source_document_id: Optional[uuid.UUID] = None
    source_document_title: Optional[str] = None
    source_chunk_id: Optional[uuid.UUID] = None
    source_page_number: Optional[int] = None
    source_snippet: Optional[str] = None


class QuizResultResponse(BaseModel):
    quiz_id: uuid.UUID
    title: str
    attempt_id: Optional[uuid.UUID] = None
    total_questions: int
    score: int
    correct_answers: int = 0
    incorrect_answers: int = 0
    percentage: float
    results: List[QuestionResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class QuizSummary(BaseModel):
    """A quiz as it appears in a history listing: metadata plus how it went.

    The attempt figures are aggregated in SQL across the student's whole history
    for this quiz, so they stay correct no matter which page the quiz is on.
    """

    id: uuid.UUID
    subject_id: uuid.UUID
    subject_title: Optional[str] = None
    title: str
    difficulty: str
    question_count: int
    topic: Optional[str] = None
    created_at: datetime
    attempt_count: int = 0
    best_percentage: float = 0.0
    last_attempt_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class QuizDetail(QuizRead):
    """A single owned quiz with its questions and the same attempt aggregates
    :class:`QuizSummary` carries, so a detail view needs one request."""

    attempt_count: int = 0
    best_percentage: float = 0.0
    last_attempt_at: Optional[datetime] = None


class QuizAttemptSummary(BaseModel):
    """A scored attempt with the identity needed to render it without a second
    request: which quiz, in which subject, and how it scored.

    Soft-deleting a quiz hides it from the quiz history but keeps its attempts
    readable here, matching the existing soft-delete contract that historical
    performance survives.
    """

    id: uuid.UUID
    quiz_id: uuid.UUID
    subject_id: uuid.UUID
    quiz_title: str
    subject_title: str
    score: int
    total_questions: int
    percentage: float
    started_at: Optional[datetime] = None
    completed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QuizAttemptAnswerRead(BaseModel):
    """The per-question snapshot stored with an attempt.

    This is the student's own submitted answer, so the explanation and correct
    option are already visible: an attempt is only ever written after grading.
    """

    id: uuid.UUID
    question_id: uuid.UUID
    question_text: str
    selected_answer: str
    correct_answer: str
    is_correct: bool
    difficulty: str
    topic: Optional[str] = None
    time_taken: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class QuizAttemptDetail(QuizAttemptSummary):
    """One owned attempt with every answer it recorded."""

    answers: List[QuizAttemptAnswerRead] = Field(default_factory=list)