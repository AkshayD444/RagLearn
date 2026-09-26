import uuid
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.subject import Subject
from app.models.user import User
from app.schemas.analytics import (
    DifficultyBreakdown,
    PerformanceSummary,
    SubjectPerformanceRead,
    TopicMasteryRead,
    WeakReasonRead,
    WeakTopicRead,
)
from app.services.student_intelligence import compute_accuracy
from app.services.weak_topics import WeakTopic, load_weak_topics

router = APIRouter(prefix="/analytics", tags=["Analytics"])

DEFAULT_WEAK_TOPIC_LIMIT = 20
MAX_WEAK_TOPIC_LIMIT = 100


async def _require_readable_subject(
    db: AsyncSession, subject_id: uuid.UUID, current_user: User
) -> None:
    """Grants access to a subject owned by the user regardless of soft-delete
    status so historical performance remains queryable after deletion."""
    result = await db.execute(
        select(Subject.id).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found or access denied.",
        )


async def _load_attempts(
    db: AsyncSession, subject_id: uuid.UUID, user_id: uuid.UUID
) -> List[QuizAttempt]:
    result = await db.execute(
        select(QuizAttempt)
        .where(
            QuizAttempt.subject_id == subject_id,
            QuizAttempt.user_id == user_id,
        )
        .order_by(QuizAttempt.completed_at.desc())
    )
    return list(result.scalars().all())


async def _load_mastery(
    db: AsyncSession, subject_id: uuid.UUID, user_id: uuid.UUID
) -> List[TopicMastery]:
    result = await db.execute(
        select(TopicMastery)
        .where(
            TopicMastery.subject_id == subject_id,
            TopicMastery.user_id == user_id,
        )
        .order_by(TopicMastery.mastery_score.desc(), TopicMastery.updated_at.desc())
    )
    return list(result.scalars().all())


async def _load_mastery_weakest_first(
    db: AsyncSession, subject_id: uuid.UUID, user_id: uuid.UUID
) -> List[TopicMastery]:
    """Same rows as :func:`_load_mastery`, ordered so the topics that need the
    most attention come first.

    Kept separate from ``_load_mastery`` so the existing ``/performance``
    response keeps its established ordering.
    """
    result = await db.execute(
        select(TopicMastery)
        .where(
            TopicMastery.subject_id == subject_id,
            TopicMastery.user_id == user_id,
        )
        .order_by(TopicMastery.mastery_score.asc(), TopicMastery.topic.asc())
    )
    return list(result.scalars().all())


async def _load_difficulty_breakdown(
    db: AsyncSession, subject_id: uuid.UUID, user_id: uuid.UUID
) -> List[DifficultyBreakdown]:
    result = await db.execute(
        select(
            QuizAttemptAnswer.difficulty,
            func.count(QuizAttemptAnswer.id),
            func.sum(case((QuizAttemptAnswer.is_correct == True, 1), else_=0)),
        )
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
        .where(
            QuizAttempt.subject_id == subject_id,
            QuizAttempt.user_id == user_id,
        )
        .group_by(QuizAttemptAnswer.difficulty)
    )
    breakdown = []
    for difficulty, attempted, correct in result.all():
        breakdown.append(
            DifficultyBreakdown(
                difficulty=difficulty,
                attempted=attempted,
                correct=correct or 0,
                accuracy=compute_accuracy(correct or 0, attempted),
            )
        )
    return breakdown


def _summarize(
    attempts: List[QuizAttempt], breakdown: List[DifficultyBreakdown]
) -> PerformanceSummary:
    total_attempts = len(attempts)
    total_questions = sum(attempt.total_questions for attempt in attempts)
    total_correct = sum(attempt.score for attempt in attempts)
    total_incorrect = total_questions - total_correct
    average_percentage = (
        round(sum(attempt.percentage for attempt in attempts) / total_attempts, 1)
        if total_attempts
        else 0.0
    )
    best_percentage = max((attempt.percentage for attempt in attempts), default=0.0)

    return PerformanceSummary(
        total_attempts=total_attempts,
        total_questions=total_questions,
        total_correct=total_correct,
        total_incorrect=total_incorrect,
        accuracy=compute_accuracy(total_correct, total_questions),
        average_percentage=average_percentage,
        best_percentage=best_percentage,
        difficulty_breakdown=breakdown,
    )


def _to_weak_topic_read(weak_topic: WeakTopic) -> WeakTopicRead:
    return WeakTopicRead(
        subject_id=weak_topic.subject_id,
        topic=weak_topic.topic,
        mastery_score=weak_topic.mastery_score,
        mastery_level=weak_topic.mastery_level,
        accuracy=weak_topic.accuracy,
        attempts=weak_topic.attempts,
        questions_attempted=weak_topic.questions_attempted,
        questions_correct=weak_topic.questions_correct,
        questions_incorrect=weak_topic.questions_incorrect,
        reasons=[
            WeakReasonRead(code=reason.code, message=reason.message)
            for reason in weak_topic.reasons
        ],
        last_attempt_at=weak_topic.last_attempt_at,
    )


@router.get("/subjects/{subject_id}/performance", response_model=SubjectPerformanceRead)
async def get_subject_performance(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Returns the current user's quiz attempts, topic mastery, and aggregated
    performance for an owned subject, including after the subject is soft-deleted."""
    await _require_readable_subject(db, subject_id, current_user)

    attempts = await _load_attempts(db, subject_id, current_user.id)
    mastery = await _load_mastery(db, subject_id, current_user.id)
    difficulty_breakdown = await _load_difficulty_breakdown(db, subject_id, current_user.id)

    return SubjectPerformanceRead(
        subject_id=subject_id,
        summary=_summarize(attempts, difficulty_breakdown),
        attempts=attempts,
        mastery=mastery,
    )


@router.get(
    "/subjects/{subject_id}/mastery", response_model=List[TopicMasteryRead]
)
async def get_topic_mastery(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Returns per-topic mastery for an owned subject, weakest topic first.

    Every topic the user has answered appears exactly once, including topics
    with no recorded attempts, so the response doubles as a progress view.
    """
    await _require_readable_subject(db, subject_id, current_user)
    mastery = await _load_mastery_weakest_first(db, subject_id, current_user.id)
    return list(mastery)


@router.get(
    "/subjects/{subject_id}/weak-topics", response_model=List[WeakTopicRead]
)
async def get_weak_topics(
    subject_id: uuid.UUID,
    limit: int = Query(default=DEFAULT_WEAK_TOPIC_LIMIT, ge=1, le=MAX_WEAK_TOPIC_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Topics needing more practice for an owned subject, with the rule that fired.

    Deterministic and rule-based: every entry lists the transparent reasons it
    was flagged, ordered by topic name rather than by a priority score.
    """
    await _require_readable_subject(db, subject_id, current_user)
    weak_topics = await load_weak_topics(
        db, user_id=current_user.id, subject_id=subject_id
    )
    return [_to_weak_topic_read(item) for item in weak_topics[:limit]]
