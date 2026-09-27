import uuid
from datetime import datetime
from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.subject import Subject
from app.schemas.subject import SubjectCreate, SubjectRead, SubjectUpdate

router = APIRouter(prefix="/subjects", tags=["Subjects"])


@router.get("", response_model=List[SubjectRead])
async def list_subjects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Retrieves all active subjects owned by the current user."""
    result = await db.execute(
        select(Subject)
        .where(
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
        .order_by(Subject.created_at.desc())
    )
    subjects = result.scalars().all()
    return subjects


@router.post("", response_model=SubjectRead, status_code=status.HTTP_201_CREATED)
async def create_subject(
    subject_in: SubjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Creates a new subject under the current user's profile."""
    subject = Subject(
        user_id=current_user.id,
        title=subject_in.title.strip(),
        description=subject_in.description.strip() if subject_in.description else "",
        color=subject_in.color or "#0284c7"
    )
    db.add(subject)
    await db.commit()
    await db.refresh(subject)
    return subject


@router.get("/{subject_id}", response_model=SubjectRead)
async def get_subject(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Gets details of a single active subject owned by the user."""
    result = await db.execute(
        select(Subject).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found."
        )
    return subject


@router.patch("/{subject_id}", response_model=SubjectRead)
async def update_subject(
    subject_id: uuid.UUID,
    subject_in: SubjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Partially updates an active subject owned by the current user.

    Only the fields present in the request body are touched, so renaming a
    subject cannot silently wipe its description. An empty body is a no-op
    rather than an error.
    """
    result = await db.execute(
        select(Subject).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found."
        )

    changes = subject_in.model_dump(exclude_unset=True)

    if "title" in changes:
        title = (changes["title"] or "").strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title cannot be empty."
            )
        subject.title = title

    if "description" in changes:
        subject.description = (changes["description"] or "").strip()

    if "color" in changes:
        color = (changes["color"] or "").strip()
        if not color:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Color cannot be empty."
            )
        subject.color = color

    await db.commit()
    await db.refresh(subject)
    return subject


@router.delete("/{subject_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_subject(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Soft-deletes a subject. The row and all historical student performance
    (quiz attempts, per-question answers, topic mastery) are preserved but the
    subject is hidden from active reads and can no longer be used."""
    result = await db.execute(
        select(Subject).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found."
        )
    subject.deleted_at = datetime.utcnow()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
