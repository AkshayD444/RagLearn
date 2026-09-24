import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.subject import Subject
from app.models.user import User
from app.rag.qa import rag_tutor_service
from app.rag.retriever import retrieve_relevant_chunks
from app.schemas.rag import (
    RagAnswerResponse,
    RagQuestion,
    RagSource,
    RetrievalRequest,
    RetrievalResponse,
    RetrievedChunk,
)

router = APIRouter(prefix="/rag", tags=["RAG"])


async def _require_owned_subject(
    db: AsyncSession, subject_id: uuid.UUID, current_user: User
) -> Subject:
    """Verifies the subject exists, is not deleted, and belongs to the current user (404 otherwise)."""
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
            detail="Subject not found or access denied.",
        )
    return subject


@router.post("/retrieve", response_model=RetrievalResponse)
async def retrieve_context(
    payload: RetrievalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Semantically retrieves the most relevant indexed chunks for a user's subject."""
    await _require_owned_subject(db, payload.subject_id, current_user)

    chunks = await retrieve_relevant_chunks(
        db,
        query=payload.query,
        subject_id=payload.subject_id,
        user_id=current_user.id,
        top_k=payload.top_k,
    )

    return RetrievalResponse(
        subject_id=payload.subject_id,
        query=payload.query,
        results=[RetrievedChunk(**chunk) for chunk in chunks],
    )


@router.post("/ask", response_model=RagAnswerResponse)
async def ask_rag_tutor(
    payload: RagQuestion,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Answers a question grounded in the user's study materials with citations."""
    await _require_owned_subject(db, payload.subject_id, current_user)

    chunks = await retrieve_relevant_chunks(
        db,
        query=payload.question,
        subject_id=payload.subject_id,
        user_id=current_user.id,
        top_k=payload.top_k,
    )

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No indexed study materials found for this subject yet. "
                "Upload and process a PDF before asking questions."
            ),
        )

    generated = await rag_tutor_service.answer_question(payload.question, chunks)

    return RagAnswerResponse(
        subject_id=payload.subject_id,
        question=payload.question,
        answer=generated["answer"],
        sources=[RagSource(**chunk) for chunk in chunks],
        model_used=generated["model_used"],
    )