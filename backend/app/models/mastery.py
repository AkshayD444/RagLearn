import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.subject import Subject
    from app.models.user import User


class TopicMastery(Base):
    """Rolled-up per-user, per-subject, per-topic performance and mastery state.

    Two evidence channels are tracked side by side and never mixed:

    * the quiz channel (``questions_*``, ``accuracy``, ``mastery_score``,
      ``mastery_level``) -- graded multiple-choice answers, consumed by the
      weak-topic detector, the adaptive recommendation engine, and the study
      planner;
    * the flashcard channel (``flashcard_*``) -- self-graded reviews, consumed
      by the flashcard progress panel.

    They are separate columns rather than a shared counter because a card review
    is self-assessed and far weaker evidence than a graded answer. Merging them
    would inflate quiz-derived mastery and silently change every
    recommendation the student sees.
    """

    __tablename__ = "topic_mastery"
    __table_args__ = (
        UniqueConstraint("user_id", "subject_id", "topic", name="uq_topic_mastery_user_subject_topic"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_incorrect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mastery_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mastery_level: Mapped[str] = mapped_column(String(20), nullable=False, default="novice")
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Flashcard evidence channel. Written only by the flashcard review flow.
    flashcard_reviews: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flashcard_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flashcard_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    flashcard_mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )