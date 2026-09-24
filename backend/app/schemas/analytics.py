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