import uuid
from datetime import date, datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.study_plan import StudyPlanSession


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    material_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    omitted_topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    sessions: Mapped[List["StudyPlanSession"]] = relationship(
        "StudyPlanSession",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="StudyPlanSession.session_number",
    )


class StudyPlanSession(Base):
    __tablename__ = "study_plan_sessions"
    __table_args__ = (
        UniqueConstraint("plan_id", "session_number", name="uq_study_plan_session_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("study_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_number: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    plan: Mapped["StudyPlan"] = relationship("StudyPlan", back_populates="sessions")
    items: Mapped[List["StudyPlanItem"]] = relationship(
        "StudyPlanItem",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="StudyPlanItem.position",
    )


class StudyPlanItem(Base):
    __tablename__ = "study_plan_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("study_plan_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    material_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_incorrect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suggested_difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    # Flashcards already scheduled to come due for this topic at plan time.
    # Informational: it does not feed the priority score, which the planner
    # inherits unchanged from the recommendation engine.
    due_cards: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    session: Mapped["StudyPlanSession"] = relationship(
        "StudyPlanSession", back_populates="items"
    )
