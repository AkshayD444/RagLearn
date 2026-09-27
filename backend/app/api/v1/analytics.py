import uuid
from typing import Any, List, NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz
from app.models.subject import Subject
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsOverviewRead,
    DifficultyBreakdown,
    LearningTimelineEntry,
    LearningTotals,
    PerformanceSummary,
    SubjectActivity,
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

# The attempt list on /performance is a recent-activity view, not the full
# history: the summary next to it is aggregated in SQL over every attempt.
PERFORMANCE_ATTEMPT_LIMIT = 50

DEFAULT_OVERVIEW_LIMIT = 10
MAX_OVERVIEW_LIMIT = 50


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
    db: AsyncSession, subject_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> List[QuizAttempt]:
    """The most recent attempts for a subject, capped at ``limit``.

    Only ever called with a bounded ``limit``. The summary next door comes from
    SQL aggregates, so the response describes the whole history even though this
    list does not.
    """
    result = await db.execute(
        select(QuizAttempt)
        .where(
            QuizAttempt.subject_id == subject_id,
            QuizAttempt.user_id == user_id,
        )
        .order_by(QuizAttempt.completed_at.desc(), QuizAttempt.id.desc())
        .limit(limit)
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


class _AttemptTotals(NamedTuple):
    attempt_count: int
    questions_answered: int
    questions_correct: int
    average_percentage: float
    best_percentage: float


async def _attempt_totals(
    db: AsyncSession, user_id: uuid.UUID, subject_id: Optional[uuid.UUID]
) -> _AttemptTotals:
    """Summed attempt figures computed by the database, not in Python.

    A student with thousands of attempts gets a single row back instead of every
    attempt being loaded to be added up.
    """
    filters = [QuizAttempt.user_id == user_id]
    if subject_id is not None:
        filters.append(QuizAttempt.subject_id == subject_id)

    row = (
        await db.execute(
            select(
                func.count(QuizAttempt.id),
                func.coalesce(func.sum(QuizAttempt.total_questions), 0),
                func.coalesce(func.sum(QuizAttempt.score), 0),
                func.avg(QuizAttempt.percentage),
                func.max(QuizAttempt.percentage),
            ).where(*filters)
        )
    ).one()

    return _AttemptTotals(
        attempt_count=int(row[0] or 0),
        questions_answered=int(row[1] or 0),
        questions_correct=int(row[2] or 0),
        average_percentage=round(float(row[3] or 0.0), 1),
        best_percentage=round(float(row[4] or 0.0), 1),
    )


def _summarize(
    totals: _AttemptTotals, breakdown: List[DifficultyBreakdown]
) -> PerformanceSummary:
    total_incorrect = totals.questions_answered - totals.questions_correct
    return PerformanceSummary(
        total_attempts=totals.attempt_count,
        total_questions=totals.questions_answered,
        total_correct=totals.questions_correct,
        total_incorrect=total_incorrect,
        accuracy=compute_accuracy(totals.questions_correct, totals.questions_answered),
        average_percentage=totals.average_percentage,
        best_percentage=totals.best_percentage,
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


@router.get("/overview", response_model=AnalyticsOverviewRead)
async def get_analytics_overview(
    subject_id: Optional[uuid.UUID] = None,
    limit: int = Query(default=DEFAULT_OVERVIEW_LIMIT, ge=1, le=MAX_OVERVIEW_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Cross-subject learning history for the dashboard.

    Every figure in ``totals`` is a SQL aggregate over the user's own rows, so
    the summary describes the whole history no matter how many attempts exist.
    The three lists are each capped at ``limit``.

    Pass ``subject_id`` to scope the whole response to one owned subject. Reads
    of a soft-deleted subject are allowed, matching the rest of this module, so
    past work stays visible.
    """
    user_id = current_user.id
    if subject_id is not None:
        await _require_readable_subject(db, subject_id, current_user)

    subject_filters = [Subject.user_id == user_id, Subject.deleted_at.is_(None)]
    quiz_filters = [Quiz.user_id == user_id, Quiz.deleted_at.is_(None)]
    if subject_id is not None:
        subject_filters.append(Subject.id == subject_id)
        quiz_filters.append(Quiz.subject_id == subject_id)

    subject_count = await db.scalar(select(func.count(Subject.id)).where(*subject_filters))
    quiz_count = await db.scalar(select(func.count(Quiz.id)).where(*quiz_filters))
    totals = await _attempt_totals(db, user_id, subject_id)

    activity = (
        await db.execute(
            select(
                func.min(QuizAttempt.completed_at),
                func.max(QuizAttempt.completed_at),
            ).where(
                QuizAttempt.user_id == user_id,
                *([QuizAttempt.subject_id == subject_id] if subject_id else []),
            )
        )
    ).one()

    # Timeline: recent attempts with their quiz and subject titles joined in, so
    # each entry renders without a follow-up request.
    timeline_rows = await db.execute(
        select(
            QuizAttempt.id,
            QuizAttempt.quiz_id,
            Quiz.title,
            QuizAttempt.subject_id,
            Subject.title,
            QuizAttempt.score,
            QuizAttempt.total_questions,
            QuizAttempt.percentage,
            QuizAttempt.completed_at,
        )
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .join(Subject, Subject.id == QuizAttempt.subject_id)
        .where(
            QuizAttempt.user_id == user_id,
            *([QuizAttempt.subject_id == subject_id] if subject_id else []),
        )
        .order_by(QuizAttempt.completed_at.desc(), QuizAttempt.id.desc())
        .limit(limit)
    )
    timeline = [
        LearningTimelineEntry(
            id=row[0],
            quiz_id=row[1],
            quiz_title=row[2],
            subject_id=row[3],
            subject_title=row[4],
            score=row[5],
            total_questions=row[6],
            percentage=row[7],
            completed_at=row[8],
        )
        for row in timeline_rows.all()
    ]

    # Subject activity: one grouped row per subject with attempts, most recent
    # first. Soft-deleted subjects are included because their history is kept.
    subject_rows = await db.execute(
        select(
            QuizAttempt.subject_id,
            Subject.title,
            func.count(QuizAttempt.id),
            func.avg(QuizAttempt.percentage),
            func.max(QuizAttempt.percentage),
            func.max(QuizAttempt.completed_at),
        )
        .join(Subject, Subject.id == QuizAttempt.subject_id)
        .where(
            QuizAttempt.user_id == user_id,
            *([QuizAttempt.subject_id == subject_id] if subject_id else []),
        )
        .group_by(QuizAttempt.subject_id, Subject.title)
        .order_by(func.max(QuizAttempt.completed_at).desc(), QuizAttempt.subject_id)
        .limit(limit)
    )
    subjects = [
        SubjectActivity(
            subject_id=row[0],
            subject_title=row[1],
            attempt_count=int(row[2] or 0),
            average_percentage=round(float(row[3] or 0.0), 1),
            best_percentage=round(float(row[4] or 0.0), 1),
            last_attempt_at=row[5],
        )
        for row in subject_rows.all()
    ]

    # Weakest topics reuse the mastery rows the rest of the module already
    # maintains, so the overview needs no second progress-tracking system.
    mastery_filters = [TopicMastery.user_id == user_id]
    if subject_id is not None:
        mastery_filters.append(TopicMastery.subject_id == subject_id)
    mastery_rows = await db.execute(
        select(TopicMastery)
        .where(*mastery_filters)
        .order_by(TopicMastery.mastery_score.asc(), TopicMastery.topic.asc())
        .limit(limit)
    )

    return AnalyticsOverviewRead(
        totals=LearningTotals(
            subject_count=int(subject_count or 0),
            quiz_count=int(quiz_count or 0),
            attempt_count=totals.attempt_count,
            questions_answered=totals.questions_answered,
            questions_correct=totals.questions_correct,
            questions_incorrect=totals.questions_answered - totals.questions_correct,
            accuracy=compute_accuracy(
                totals.questions_correct, totals.questions_answered
            ),
            average_percentage=totals.average_percentage,
            best_percentage=totals.best_percentage,
            first_activity_at=activity[0],
            last_activity_at=activity[1],
        ),
        timeline=timeline,
        subjects=subjects,
        weakest_topics=[TopicMasteryRead.model_validate(m) for m in mastery_rows.scalars().all()],
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

    totals = await _attempt_totals(db, current_user.id, subject_id)
    mastery = await _load_mastery(db, subject_id, current_user.id)
    difficulty_breakdown = await _load_difficulty_breakdown(db, subject_id, current_user.id)
    attempts = await _load_attempts(
        db, subject_id, current_user.id, PERFORMANCE_ATTEMPT_LIMIT
    )

    return SubjectPerformanceRead(
        subject_id=subject_id,
        summary=_summarize(totals, difficulty_breakdown),
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
