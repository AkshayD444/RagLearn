import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz
from app.models.user import User

EVIDENCE_TARGET = 10
UNTAGGED_TOPIC = "General"

MASTERY_LEVEL_THRESHOLDS = (
    ("mastered", 85.0),
    ("proficient", 70.0),
    ("developing", 40.0),
)


def normalize_topic(topic: Optional[str]) -> str:
    """Normalizes optional free-text topic metadata into a stable bucket label."""
    if not topic:
        return UNTAGGED_TOPIC
    cleaned = topic.strip()
    return cleaned or UNTAGGED_TOPIC


@dataclass
class AnswerEvaluation:
    """Per-question scoring snapshot produced by the quiz submission flow."""
    question_id: uuid.UUID
    question_text: str
    selected_answer: str
    correct_answer: str
    is_correct: bool
    difficulty: str
    topic: Optional[str]
    time_taken: Optional[float] = None


def compute_accuracy(correct: int, attempted: int) -> float:
    """Percentage accuracy (0-100)."""
    if attempted <= 0:
        return 0.0
    return round(correct / attempted * 100, 1)


def compute_mastery(correct: int, attempted: int) -> float:
    """Confidence-aware 0-100 mastery score.

    accuracy = correct / attempted
    confidence grows linearly from 0 to 1 as evidence accumulates toward
    EVIDENCE_TARGET questions.
    mastery = accuracy * 100 * min(1, attempted / EVIDENCE_TARGET)
    """
    if attempted <= 0:
        return 0.0
    accuracy = correct / attempted
    confidence = min(1.0, attempted / EVIDENCE_TARGET)
    return round(accuracy * confidence * 100, 1)


def mastery_level(score: float) -> str:
    """Maps a mastery score onto a coarse, human-readable level."""
    for level, threshold in MASTERY_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "novice"


async def _get_or_create_mastery(
    db: AsyncSession, *, user_id: uuid.UUID, subject_id: uuid.UUID, topic: str
) -> TopicMastery:
    result = await db.execute(
        select(TopicMastery).where(
            TopicMastery.user_id == user_id,
            TopicMastery.subject_id == subject_id,
            TopicMastery.topic == topic,
        )
    )
    mastery = result.scalar_one_or_none()
    if mastery is None:
        mastery = TopicMastery(
            user_id=user_id,
            subject_id=subject_id,
            topic=topic,
            last_attempt_at=None,
        )
        db.add(mastery)
        await db.flush()
    return mastery


async def _refresh_mastery(mastery: TopicMastery, last_attempt_at: datetime) -> None:
    mastery.accuracy = compute_accuracy(
        mastery.questions_correct, mastery.questions_attempted
    )
    mastery.mastery_score = compute_mastery(
        mastery.questions_correct, mastery.questions_attempted
    )
    mastery.mastery_level = mastery_level(mastery.mastery_score)
    mastery.last_attempt_at = last_attempt_at
    mastery.updated_at = datetime.utcnow()


async def persist_attempt(
    db: AsyncSession,
    *,
    user: User,
    quiz: Quiz,
    evaluations: List[AnswerEvaluation],
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
) -> QuizAttempt:
    """Stores a scored attempt, its per-question snapshots, and updates topic mastery.

    The attempt inherits the quiz owner (user.id) so attempts can never be
    attached to a different authenticated user.
    """
    total = len(evaluations)
    score = sum(1 for evaluation in evaluations if evaluation.is_correct)
    percentage = round(score / total * 100, 1) if total else 0.0

    resolved_completed_at = completed_at or datetime.utcnow()
    attempt = QuizAttempt(
        user_id=user.id,
        quiz_id=quiz.id,
        subject_id=quiz.subject_id,
        score=score,
        total_questions=total,
        percentage=percentage,
        started_at=started_at,
        completed_at=resolved_completed_at,
    )
    db.add(attempt)
    await db.flush()

    seen_topics = set()
    for evaluation in evaluations:
        topic = normalize_topic(evaluation.topic)
        db.add(
            QuizAttemptAnswer(
                attempt_id=attempt.id,
                question_id=evaluation.question_id,
                question_text=evaluation.question_text,
                selected_answer=evaluation.selected_answer,
                correct_answer=evaluation.correct_answer,
                is_correct=evaluation.is_correct,
                difficulty=evaluation.difficulty,
                topic=topic,
                time_taken=evaluation.time_taken,
            )
        )

        mastery = await _get_or_create_mastery(
            db, user_id=user.id, subject_id=quiz.subject_id, topic=topic
        )
        mastery.questions_attempted += 1
        if evaluation.is_correct:
            mastery.questions_correct += 1
        else:
            mastery.questions_incorrect += 1
        if topic not in seen_topics:
            mastery.attempts += 1
            seen_topics.add(topic)
        await _refresh_mastery(mastery, resolved_completed_at)

    return attempt