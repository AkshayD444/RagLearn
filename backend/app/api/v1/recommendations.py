import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user, require_owned_subject
from app.models.user import User
from app.schemas.recommendation import (
    NextLearningRead,
    NextLearningRecommendationRead,
    RecommendationRead,
)
from app.services.adaptive_learning import (
    Recommendation,
    build_recommendations,
    load_metrics,
)
from app.services.next_learning import (
    INSUFFICIENT_DATA_REASON,
    NextLearningRecommendation,
    recommend_next_learning,
)

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])

DEFAULT_LIMIT = 5
MAX_LIMIT = 50


def _to_read(recommendation: Recommendation) -> RecommendationRead:
    return RecommendationRead(
        subject_id=recommendation.subject_id,
        subject_title=recommendation.subject_title,
        topic=recommendation.topic,
        priority=recommendation.priority,
        mastery=recommendation.mastery,
        mastery_level=recommendation.mastery_level,
        accuracy=recommendation.accuracy,
        attempts=recommendation.attempts,
        questions_attempted=recommendation.questions_attempted,
        questions_incorrect=recommendation.questions_incorrect,
        recommendation_type=recommendation.recommendation_type,
        reason=recommendation.reason,
        suggested_action=recommendation.suggested_action,
        suggested_difficulty=recommendation.suggested_difficulty,
        last_attempt_at=recommendation.last_attempt_at,
    )


def _to_next_read(
    recommendation: Optional[NextLearningRecommendation],
) -> NextLearningRead:
    """Wraps the service result in the statused envelope the client renders.

    ``None`` from the service means there is no evidence to rank, which the
    client should show as an empty state, not as data it is missing.
    """
    if recommendation is None:
        return NextLearningRead(
            status="insufficient_data",
            reason=INSUFFICIENT_DATA_REASON,
            recommendation=None,
        )
    return NextLearningRead(
        status="ok",
        reason=recommendation.reason,
        recommendation=NextLearningRecommendationRead(
            subject_id=recommendation.subject_id,
            subject_title=recommendation.subject_title,
            topic=recommendation.topic,
            priority=recommendation.priority,
            mastery=recommendation.mastery,
            mastery_level=recommendation.mastery_level,
            accuracy=recommendation.accuracy,
            weighted_accuracy=recommendation.weighted_accuracy,
            difficulty_factor=recommendation.difficulty_factor,
            questions_attempted=recommendation.questions_attempted,
            activity_type=recommendation.activity_type,
            reason=recommendation.reason,
            suggested_difficulty=recommendation.suggested_difficulty,
            due_cards=recommendation.due_cards,
            last_attempt_at=recommendation.last_attempt_at,
        ),
    )


@router.get("/subjects/{subject_id}", response_model=List[RecommendationRead])
async def get_subject_recommendations(
    subject_id: uuid.UUID,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Deterministic adaptive recommendations for one active subject owned by
    the current user. Deleted or foreign subjects return 404."""
    await require_owned_subject(db, subject_id, current_user)
    metrics = await load_metrics(db, current_user.id, subject_id=subject_id)
    recommendations = build_recommendations(metrics)[:limit]
    return [_to_read(rec) for rec in recommendations]


@router.get("/top", response_model=List[RecommendationRead])
async def get_top_recommendations(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Top recommended topics across every active subject of the current user.
    Results are deterministic and only ever reference the authenticated user's
    own, non-deleted subjects and topics."""
    metrics = await load_metrics(db, current_user.id)
    recommendations = build_recommendations(metrics)[:limit]
    return [_to_read(rec) for rec in recommendations]


@router.get("/next", response_model=NextLearningRead)
async def get_next_learning(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """The single best next learning step across the current user's subjects.

    Returns a statused envelope: ``status: ok`` with a recommendation, or
    ``status: insufficient_data`` when there is no learning history yet. The
    selection is deterministic and stays within the user's own active subjects.
    """
    recommendation = await recommend_next_learning(db, user_id=current_user.id)
    return _to_next_read(recommendation)


@router.get("/subjects/{subject_id}/next", response_model=NextLearningRead)
async def get_subject_next_learning(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """The best next learning step within one owned, non-deleted subject.

    Subject access is enforced exactly like the existing recommendations
    endpoints: a deleted or foreign subject returns 404, and the result never
    references data outside that subject.
    """
    await require_owned_subject(db, subject_id, current_user)
    recommendation = await recommend_next_learning(
        db, user_id=current_user.id, subject_id=subject_id
    )
    return _to_next_read(recommendation)