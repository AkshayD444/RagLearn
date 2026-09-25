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