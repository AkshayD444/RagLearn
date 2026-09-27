import math
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.study_plan import StudyPlan, StudyPlanItem, StudyPlanSession
from app.models.subject import Subject
from app.models.user import User
from app.services.adaptive_learning import (
    MIN_REVIEW_EVIDENCE,
    Recommendation,
    build_recommendations,
    load_metrics,
)
from app.services.flashcard_schedule import due_counts_by_topic

DEFAULT_MAX_TOPICS_PER_SESSION = 3
LEARN = "LEARN"
PRACTICE = "PRACTICE"
REVIEW = "REVIEW"
MAINTAIN = "MAINTAIN"

ESTIMATED_MINUTES = {
    LEARN: 20,
    PRACTICE: 15,
    REVIEW: 15,
    MAINTAIN: 10,
}


class StudyPlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class StudyPlanItemDraft:
    topic: str
    action: str
    priority: float
    estimated_minutes: int
    reason: str
    material_available: bool
    mastery: float
    accuracy: float
    attempts: int
    questions_attempted: int
    questions_incorrect: int
    suggested_difficulty: str
    # How many flashcards are already scheduled to be due for this topic.
    # Defaults to 0 because `build_study_plan_draft` is pure and knows nothing
    # about the flashcard schedule; `persist_study_plan` fills it in from the
    # database. It never feeds `priority` -- that comes from the recommendation
    # engine unchanged, so adding scheduling cannot re-rank an existing plan.
    due_cards: int = 0


@dataclass(frozen=True)
class StudyPlanSessionDraft:
    session_number: int
    scheduled_date: Optional[date]
    estimated_minutes: int
    items: tuple[StudyPlanItemDraft, ...]


@dataclass(frozen=True)
class StudyPlanDraft:
    subject_id: uuid.UUID
    target_date: Optional[date]
    material_available: bool
    omitted_topic_count: int
    sessions: tuple[StudyPlanSessionDraft, ...]


def _action_for(
    recommendation: Recommendation, material_available: bool
) -> tuple[str, str]:
    """Selects an action from the existing recommendation without recalculating scores."""
    if not material_available:
        return (
            LEARN,
            f"{recommendation.reason} Indexed study material is unavailable; upload a document before learning or practicing this topic.",
        )
    if (
        recommendation.recommendation_type == REVIEW
        and recommendation.questions_attempted < MIN_REVIEW_EVIDENCE
    ):
        return (
            LEARN,
            f"{recommendation.reason} Not enough questions yet — review indexed subject material before treating this topic as a review priority.",
        )
    if recommendation.recommendation_type == REVIEW:
        return REVIEW, recommendation.reason
    if (
        recommendation.recommendation_type == PRACTICE
        and recommendation.questions_attempted < MIN_REVIEW_EVIDENCE
    ):
        return (
            LEARN,
            f"{recommendation.reason} Review indexed subject material first, then practice this topic.",
        )
    return recommendation.recommendation_type, recommendation.reason


def _item_from_recommendation(
    recommendation: Recommendation, material_available: bool
) -> StudyPlanItemDraft:
    action, reason = _action_for(recommendation, material_available)
    return StudyPlanItemDraft(
        topic=recommendation.topic,
        action=action,
        priority=recommendation.priority,
        estimated_minutes=ESTIMATED_MINUTES[action],
        reason=reason,
        material_available=material_available,
        mastery=recommendation.mastery,
        accuracy=recommendation.accuracy,
        attempts=recommendation.attempts,
        questions_attempted=recommendation.questions_attempted,
        questions_incorrect=recommendation.questions_incorrect,
        suggested_difficulty=recommendation.suggested_difficulty,
    )


def _pack_items(
    items: Sequence[StudyPlanItemDraft],
    max_topics_per_session: int,
    available_minutes_per_day: Optional[int],
) -> List[List[StudyPlanItemDraft]]:
    groups: List[List[StudyPlanItemDraft]] = []
    current: List[StudyPlanItemDraft] = []
    current_minutes = 0

    for item in items:
        exceeds_topic_limit = len(current) >= max_topics_per_session
        exceeds_time_limit = (
            available_minutes_per_day is not None
            and current
            and current_minutes + item.estimated_minutes > available_minutes_per_day
        )
        if current and (exceeds_topic_limit or exceeds_time_limit):
            groups.append(current)
            current = []
            current_minutes = 0
        current.append(item)
        current_minutes += item.estimated_minutes

    if current:
        groups.append(current)
    return groups


def _scheduled_date(
    start_date: date, target_date: Optional[date], session_index: int, session_count: int
) -> Optional[date]:
    if target_date is None:
        return None
    available_days = (target_date - start_date).days
    if available_days <= 0 or session_count <= 1:
        return start_date
    offset = (session_index * available_days) // (session_count - 1)
    return start_date + timedelta(days=offset)


def build_study_plan_draft(
    subject: Subject,
    recommendations: Sequence[Recommendation],
    *,
    target_date: Optional[date] = None,
    available_minutes_per_day: Optional[int] = None,
    requested_sessions: Optional[int] = None,
    max_topics_per_session: int = DEFAULT_MAX_TOPICS_PER_SESSION,
    today: Optional[date] = None,
    material_available: bool = True,
) -> StudyPlanDraft:
    """Builds a deterministic, capacity-aware study sequence.

    The engine consumes the ordered recommendations from the Adaptive Learning
    Engine. It never recalculates mastery, accuracy, or priority. With a target
    date, sessions are spread from today through that date. Without one, session
    order is the only schedule. The requested session count and topic limit cap
    the highest-priority items; lower-priority items are reported as omitted.
    """
    if max_topics_per_session < 1:
        raise StudyPlanValidationError("Maximum topics per session must be at least one.")
    if requested_sessions is not None and requested_sessions < 1:
        raise StudyPlanValidationError("Requested sessions must be at least one.")
    if available_minutes_per_day is not None and available_minutes_per_day < 1:
        raise StudyPlanValidationError("Available study time must be at least one minute.")

    start_date = today or date.today()
    if target_date is not None and target_date < start_date:
        raise StudyPlanValidationError("Target date must be today or later.")

    ordered = list(recommendations)
    if not ordered:
        return StudyPlanDraft(
            subject_id=subject.id,
            target_date=target_date,
            material_available=material_available,
            omitted_topic_count=0,
            sessions=(),
        )

    if requested_sessions is None:
        session_limit = max(
            1, math.ceil(len(ordered) / max_topics_per_session)
        )
    else:
        session_limit = requested_sessions

    if target_date is not None:
        available_days = (target_date - start_date).days + 1
        session_limit = min(session_limit, available_days)

    selected = ordered[: session_limit * max_topics_per_session]
    item_drafts = [
        _item_from_recommendation(recommendation, material_available)
        for recommendation in selected
    ]
    if available_minutes_per_day is not None:
        longest_item = max(
            (item.estimated_minutes for item in item_drafts), default=0
        )
        if longest_item > available_minutes_per_day:
            raise StudyPlanValidationError(
                f"Available study time must be at least {longest_item} minutes "
                "for the longest planned topic."
            )
    groups = _pack_items(
        item_drafts, max_topics_per_session, available_minutes_per_day
    )
    while len(groups) > session_limit and item_drafts:
        item_drafts.pop()
        groups = _pack_items(
            item_drafts, max_topics_per_session, available_minutes_per_day
        )

    sessions = tuple(
        StudyPlanSessionDraft(
            session_number=index,
            scheduled_date=_scheduled_date(
                start_date, target_date, index - 1, len(groups)
            ),
            estimated_minutes=sum(item.estimated_minutes for item in group),
            items=tuple(group),
        )
        for index, group in enumerate(groups, start=1)
    )
    return StudyPlanDraft(
        subject_id=subject.id,
        target_date=target_date,
        material_available=material_available,
        omitted_topic_count=len(ordered) - len(item_drafts),
        sessions=sessions,
    )


async def has_indexed_material(
    db: AsyncSession, user_id: uuid.UUID, subject_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(func.count(DocumentChunk.id))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.user_id == user_id,
            DocumentChunk.subject_id == subject_id,
            Document.user_id == user_id,
            Document.status == "READY",
        )
    )
    return bool(result.scalar_one())


async def persist_study_plan(
    db: AsyncSession,
    *,
    user: User,
    subject: Subject,
    target_date: Optional[date],
    available_minutes_per_day: Optional[int],
    requested_sessions: Optional[int],
    max_topics_per_session: int,
) -> uuid.UUID:
    if subject.user_id != user.id:
        raise StudyPlanValidationError("Subject is not available to this user.")

    metrics = await load_metrics(db, user.id, subject_id=subject.id)
    recommendations = build_recommendations(metrics)
    material_available = await has_indexed_material(db, user.id, subject.id)
    created_at = datetime.utcnow()
    draft = build_study_plan_draft(
        subject,
        recommendations,
        target_date=target_date,
        available_minutes_per_day=available_minutes_per_day,
        requested_sessions=requested_sessions,
        max_topics_per_session=max_topics_per_session,
        today=created_at.date(),
        material_available=material_available,
    )

    # The draft is built from quiz evidence alone, so the due-card counts are
    # layered on afterwards. That keeps `build_study_plan_draft` pure and leaves
    # the existing plan ordering and priorities untouched.
    due_by_topic = await due_counts_by_topic(
        db, user_id=user.id, subject_id=subject.id, now=created_at
    )
    if due_by_topic:
        draft = replace(
            draft,
            sessions=tuple(
                replace(
                    session,
                    items=tuple(
                        replace(item, due_cards=due_by_topic.get(item.topic, 0))
                        for item in session.items
                    ),
                )
                for session in draft.sessions
            ),
        )

    plan = StudyPlan(
        user_id=user.id,
        subject_id=subject.id,
        subject_title=subject.title,
        target_date=target_date,
        material_available=material_available,
        omitted_topic_count=draft.omitted_topic_count,
        created_at=created_at,
    )
    db.add(plan)
    await db.flush()

    for session in draft.sessions:
        session_row = StudyPlanSession(
            plan_id=plan.id,
            session_number=session.session_number,
            scheduled_date=session.scheduled_date,
        )
        db.add(session_row)
        await db.flush()
        for position, item in enumerate(session.items, start=1):
            db.add(
                StudyPlanItem(
                    session_id=session_row.id,
                    position=position,
                    topic=item.topic,
                    action=item.action,
                    priority=item.priority,
                    estimated_minutes=item.estimated_minutes,
                    reason=item.reason,
                    material_available=item.material_available,
                    mastery=item.mastery,
                    accuracy=item.accuracy,
                    attempts=item.attempts,
                    questions_attempted=item.questions_attempted,
                    questions_incorrect=item.questions_incorrect,
                    suggested_difficulty=item.suggested_difficulty,
                    due_cards=item.due_cards,
                )
            )

    await db.commit()
    return plan.id


async def load_study_plan(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan_id: Optional[uuid.UUID] = None,
    subject_id: Optional[uuid.UUID] = None,
) -> Optional[StudyPlan]:
    statement = (
        select(StudyPlan)
        .join(Subject, Subject.id == StudyPlan.subject_id)
        .options(
            selectinload(StudyPlan.sessions).selectinload(StudyPlanSession.items)
        )
        .where(
            StudyPlan.user_id == user_id,
            Subject.user_id == user_id,
            Subject.deleted_at.is_(None),
        )
    )
    if plan_id is not None:
        statement = statement.where(StudyPlan.id == plan_id)
    if subject_id is not None:
        statement = statement.where(StudyPlan.subject_id == subject_id)
    statement = statement.order_by(StudyPlan.created_at.desc(), StudyPlan.id.desc()).limit(1)
    result = await db.execute(statement)
    return result.scalar_one_or_none()
