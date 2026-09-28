import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class RecommendationRead(BaseModel):
    """A single adaptive learning recommendation for the current user."""

    subject_id: uuid.UUID
    subject_title: str
    topic: str
    priority: float
    mastery: float
    mastery_level: str
    accuracy: float
    attempts: int
    questions_attempted: int
    questions_incorrect: int
    recommendation_type: str
    reason: str
    suggested_action: str
    suggested_difficulty: str
    last_attempt_at: Optional[datetime] = None


class NextLearningRead(BaseModel):
    """The single best next step for the current user.

    ``status`` is ``ok`` when a recommendation exists and ``insufficient_data``
    when there is no learning history yet; ``recommendation`` is null in the
    latter case so the client can render one empty state instead of branching
    on a missing schema.
    """

    status: str
    reason: str
    recommendation: Optional["NextLearningRecommendationRead"] = None


class NextLearningRecommendationRead(BaseModel):
    """The winner of the next-learning selection, with its chosen activity."""

    subject_id: uuid.UUID
    subject_title: str
    topic: str
    priority: float
    mastery: float
    mastery_level: str
    accuracy: float
    weighted_accuracy: float
    difficulty_factor: float
    questions_attempted: int
    activity_type: str
    reason: str
    suggested_difficulty: str
    due_cards: int
    last_attempt_at: Optional[datetime] = None