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