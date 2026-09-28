import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class DifficultyBreakdown(BaseModel):
    difficulty: str
    attempted: int
    correct: int
    accuracy: float


class PerformanceSummary(BaseModel):
    total_attempts: int = 0
    total_questions: int = 0
    total_correct: int = 0
    total_incorrect: int = 0
    accuracy: float = 0.0
    average_percentage: float = 0.0
    best_percentage: float = 0.0
    difficulty_breakdown: List[DifficultyBreakdown] = Field(default_factory=list)


class QuizAttemptRead(BaseModel):
    id: uuid.UUID
    quiz_id: uuid.UUID
    subject_id: uuid.UUID
    score: int
    total_questions: int
    percentage: float
    started_at: Optional[datetime] = None
    completed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TopicMasteryRead(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    topic: str
    attempts: int
    questions_attempted: int
    questions_correct: int
    questions_incorrect: int
    accuracy: float
    mastery_score: float
    mastery_level: str
    last_attempt_at: Optional[datetime] = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SubjectPerformanceRead(BaseModel):
    subject_id: uuid.UUID
    summary: PerformanceSummary
    attempts: List[QuizAttemptRead] = Field(default_factory=list)
    mastery: List[TopicMasteryRead] = Field(default_factory=list)


class WeakReasonRead(BaseModel):
    """A single rule that flagged a topic, with the numbers that triggered it."""

    code: str
    message: str


class WeakTopicRead(BaseModel):
    subject_id: uuid.UUID
    topic: str
    mastery_score: float
    mastery_level: str
    accuracy: float
    attempts: int
    questions_attempted: int
    questions_correct: int
    questions_incorrect: int
    reasons: List[WeakReasonRead] = Field(default_factory=list)
    #: ``confirmed`` when an aggregate-count rule fired, ``low`` otherwise.
    confidence: str = "low"
    last_attempt_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Cross-subject overview
# ---------------------------------------------------------------------------


class LearningTotals(BaseModel):
    """Lifetime totals for a student's learning, aggregated in the database.

    Every figure is a SQL aggregate over the student's own rows, so the numbers
    describe the whole history regardless of how many rows exist. ``accuracy``
    and ``average_percentage`` are both 0-100.
    """

    subject_count: int = 0
    quiz_count: int = 0
    attempt_count: int = 0
    questions_answered: int = 0
    questions_correct: int = 0
    questions_incorrect: int = 0
    accuracy: float = 0.0
    average_percentage: float = 0.0
    best_percentage: float = 0.0
    #: Study-material figures so the dashboard headline "material" numbers come
    #: from the same response as the rest of the totals.
    document_count: int = 0
    ready_document_count: int = 0
    chunk_count: int = 0
    #: Cards currently in the review queue (never-studied cards included).
    due_flashcard_count: int = 0
    #: Mean ``mastery_score`` across every topic the student has answered,
    #: 0-100. ``0.0`` when no topic has any recorded attempt.
    average_topic_mastery: float = 0.0
    first_activity_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None


class LearningTimelineEntry(BaseModel):
    """One row of the progress timeline: a single completed attempt.

    The quiz and subject titles are joined in rather than looked up, so a
    timeline entry is renderable on its own.
    """

    id: uuid.UUID
    quiz_id: uuid.UUID
    quiz_title: str
    subject_id: uuid.UUID
    subject_title: str
    score: int
    total_questions: int
    percentage: float
    completed_at: datetime


class SubjectActivity(BaseModel):
    """Per-subject rollup for the overview, ordered by most recent activity."""

    subject_id: uuid.UUID
    subject_title: str
    attempt_count: int = 0
    average_percentage: float = 0.0
    best_percentage: float = 0.0
    last_attempt_at: Optional[datetime] = None


class AnalyticsOverviewRead(BaseModel):
    """Dashboard-level learning history: totals, a bounded timeline, the
    subjects with activity, and the topics most in need of work.

    The three lists are each capped by the request's ``limit``; ``totals`` always
    describes the entire history.
    """

    totals: LearningTotals = Field(default_factory=LearningTotals)
    timeline: List[LearningTimelineEntry] = Field(default_factory=list)
    subjects: List[SubjectActivity] = Field(default_factory=list)
    weakest_topics: List[TopicMasteryRead] = Field(default_factory=list)
