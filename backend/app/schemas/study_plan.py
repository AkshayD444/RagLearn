import uuid
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StudyPlanAction(str, Enum):
    LEARN = "LEARN"
    PRACTICE = "PRACTICE"
    REVIEW = "REVIEW"
    MAINTAIN = "MAINTAIN"


class StudyPlanOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_date: Optional[date] = Field(
        default=None,
        validation_alias=AliasChoices("target_date", "exam_date"),
    )
    available_minutes_per_day: Optional[int] = Field(
        default=None,
        ge=1,
        le=1440,
        validation_alias=AliasChoices(
            "available_minutes_per_day",
            "available_study_time_minutes",
            "available_study_time",
        ),
    )
    requested_sessions: Optional[int] = Field(
        default=None,
        ge=1,
        le=60,
        validation_alias=AliasChoices("requested_sessions", "session_count", "sessions"),
    )
    max_topics_per_session: Optional[int] = Field(
        default=None,
        ge=1,
        le=30,
        validation_alias=AliasChoices(
            "max_topics_per_session", "max_topics", "topic_limit"
        ),
    )


class StudyPlanGenerateRequest(StudyPlanOptions):
    subject_id: uuid.UUID


class StudyPlanItemRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    topic: str
    action: StudyPlanAction
    priority: float
    estimated_minutes: int
    reason: str
    completed: bool
    completed_at: Optional[datetime] = None
    material_available: bool
    mastery: float
    accuracy: float
    attempts: int
    questions_attempted: int
    questions_incorrect: int
    suggested_difficulty: str
    # Flashcards already scheduled to be due for this topic when the plan was
    # built. Informational only: it does not affect `priority`, which comes
    # from the recommendation engine unchanged.
    due_cards: int = 0


class StudyPlanSessionRead(BaseModel):
    id: uuid.UUID
    session_number: int
    scheduled_date: Optional[date] = None
    estimated_minutes: int
    items: List[StudyPlanItemRead] = Field(default_factory=list)


class StudyPlanRead(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    subject_title: str
    generated_at: datetime
    created_at: datetime
    target_date: Optional[date] = None
    material_available: bool
    omitted_topic_count: int = 0
    sessions: List[StudyPlanSessionRead] = Field(default_factory=list)


class StudyPlanItemCompletionRequest(BaseModel):
    completed: bool = True
