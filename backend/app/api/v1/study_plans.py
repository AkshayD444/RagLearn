import uuid
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_owned_subject
from app.models.study_plan import StudyPlan, StudyPlanItem, StudyPlanSession
from app.models.subject import Subject
from app.models.user import User
from app.schemas.study_plan import (
    StudyPlanGenerateRequest,
    StudyPlanItemCompletionRequest,
    StudyPlanItemRead,
    StudyPlanOptions,
    StudyPlanRead,
    StudyPlanSessionRead,
)
from app.services.study_planner import (
    StudyPlanValidationError,
    load_study_plan,
    persist_study_plan,
)

router = APIRouter(prefix="/study-plans", tags=["Study Plans"])


def _item_to_read(item: StudyPlanItem) -> StudyPlanItemRead:
    return StudyPlanItemRead(
        id=item.id,
        session_id=item.session_id,
        topic=item.topic,
        action=item.action,
        priority=item.priority,
        estimated_minutes=item.estimated_minutes,
        reason=item.reason,
        completed=item.completed,
        completed_at=item.completed_at,
        material_available=item.material_available,
        mastery=item.mastery,
        accuracy=item.accuracy,
        attempts=item.attempts,
        questions_attempted=item.questions_attempted,
        questions_incorrect=item.questions_incorrect,
        suggested_difficulty=item.suggested_difficulty,
    )


def _plan_to_read(plan: StudyPlan) -> StudyPlanRead:
    sessions = [
        StudyPlanSessionRead(
            id=session.id,
            session_number=session.session_number,
            scheduled_date=session.scheduled_date,
            estimated_minutes=sum(item.estimated_minutes for item in session.items),
            items=[_item_to_read(item) for item in sorted(session.items, key=lambda item: item.position)],
        )
        for session in sorted(plan.sessions, key=lambda session: session.session_number)
    ]
    return StudyPlanRead(
        id=plan.id,
        subject_id=plan.subject_id,
        subject_title=plan.subject_title,
        generated_at=plan.created_at,
        created_at=plan.created_at,
        target_date=plan.target_date,
        material_available=plan.material_available,
        omitted_topic_count=plan.omitted_topic_count,
        sessions=sessions,
    )


async def _generate_plan(
    *,
    subject: Subject,
    payload: StudyPlanOptions,
    db: AsyncSession,
    current_user: User,
) -> StudyPlanRead:
    try:
        plan_id = await persist_study_plan(
            db,
            user=current_user,
            subject=subject,
            target_date=payload.target_date,
            available_minutes_per_day=payload.available_minutes_per_day,
            requested_sessions=payload.requested_sessions,
            max_topics_per_session=payload.max_topics_per_session
            or 3,
        )
    except StudyPlanValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    plan = await load_study_plan(
        db, user_id=current_user.id, plan_id=plan_id
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study plan not found.",
        )
    return _plan_to_read(plan)


@router.post("/generate", response_model=StudyPlanRead, status_code=status.HTTP_201_CREATED)
async def generate_study_plan(
    payload: StudyPlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    subject = await require_owned_subject(db, payload.subject_id, current_user)
    return await _generate_plan(subject=subject, payload=payload, db=db, current_user=current_user)


@router.post("", response_model=StudyPlanRead, status_code=status.HTTP_201_CREATED)
async def create_study_plan(
    payload: StudyPlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    subject = await require_owned_subject(db, payload.subject_id, current_user)
    return await _generate_plan(subject=subject, payload=payload, db=db, current_user=current_user)


@router.post(
    "/subjects/{subject_id}",
    response_model=StudyPlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate_subject_study_plan(
    subject_id: uuid.UUID,
    payload: Optional[StudyPlanOptions] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    subject = await require_owned_subject(db, subject_id, current_user)
    return await _generate_plan(
        subject=subject,
        payload=payload or StudyPlanOptions(),
        db=db,
        current_user=current_user,
    )


@router.get("/latest", response_model=StudyPlanRead)
async def get_latest_study_plan(
    subject_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    if subject_id is not None:
        await require_owned_subject(db, subject_id, current_user)
    plan = await load_study_plan(
        db, user_id=current_user.id, subject_id=subject_id
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No study plan found.",
        )
    return _plan_to_read(plan)


@router.get("/subjects/{subject_id}/latest", response_model=StudyPlanRead)
@router.get("/subjects/{subject_id}", response_model=StudyPlanRead)
async def get_latest_subject_study_plan(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    await require_owned_subject(db, subject_id, current_user)
    plan = await load_study_plan(
        db, user_id=current_user.id, subject_id=subject_id
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No study plan found.",
        )
    return _plan_to_read(plan)


@router.patch("/items/{item_id}/complete", response_model=StudyPlanRead)
@router.post("/items/{item_id}/complete", response_model=StudyPlanRead)
async def complete_study_plan_item(
    item_id: uuid.UUID,
    payload: Optional[StudyPlanItemCompletionRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(StudyPlanItem, StudyPlan.id)
        .join(StudyPlanSession, StudyPlanSession.id == StudyPlanItem.session_id)
        .join(StudyPlan, StudyPlan.id == StudyPlanSession.plan_id)
        .join(Subject, Subject.id == StudyPlan.subject_id)
        .where(
            StudyPlanItem.id == item_id,
            StudyPlan.user_id == current_user.id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study plan item not found or access denied.",
        )

    item, plan_id = row
    completed = payload.completed if payload is not None else True
    item.completed = completed
    item.completed_at = datetime.utcnow() if completed else None
    await db.commit()

    plan = await load_study_plan(db, user_id=current_user.id, plan_id=plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study plan not found or access denied.",
        )
    return _plan_to_read(plan)
