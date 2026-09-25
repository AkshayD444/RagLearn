import uuid
from typing import Any, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user, require_owned_subject
from app.models.user import User
from app.schemas.recommendation import RecommendationRead
from app.services.adaptive_learning import (
    Recommendation,
    build_recommendations,
    load_metrics,
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