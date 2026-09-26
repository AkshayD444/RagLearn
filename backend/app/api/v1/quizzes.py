import uuid
from datetime import datetime
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user, require_owned_subject
from app.models.quiz import Quiz, QuizQuestion
from app.models.user import User
from app.schemas.quiz import (
    AnswerItem,
    QuestionResult,
    QuizGenerateRequest,
    QuizQuestionRead,
    QuizRead,
    QuizResultResponse,
    QuizSubmission,
)
from app.services.quiz_engine import NoStudyMaterialsError, quiz_generation_service
from app.services.student_intelligence import (
    AnswerEvaluation,
    persist_attempt,
    score_evaluations,
)

router = APIRouter(prefix="/quizzes", tags=["Quizzes"])


async def _load_questions(db: AsyncSession, quiz_id: uuid.UUID) -> List[QuizQuestion]:
    result = await db.execute(
        select(QuizQuestion)
        .where(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.created_at, QuizQuestion.id)
    )
    return list(result.scalars().all())


async def _load_owned_quiz(
    db: AsyncSession, quiz_id: uuid.UUID, current_user: User
) -> Quiz:
    result = await db.execute(
        select(Quiz).where(
            Quiz.id == quiz_id,
            Quiz.user_id == current_user.id,
            Quiz.deleted_at.is_(None),
        )
    )
    quiz = result.scalar_one_or_none()
    if not quiz:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found or access denied.",
        )
    return quiz


def _to_question_read(question: QuizQuestion) -> QuizQuestionRead:
    return QuizQuestionRead(
        id=question.id,
        question_text=question.question_text,
        option_a=question.option_a,
        option_b=question.option_b,
        option_c=question.option_c,
        option_d=question.option_d,
        difficulty=question.difficulty,
        source_document_id=question.document_id,
        source_document_title=question.document_title,
        source_chunk_id=question.chunk_id,
        source_page_number=question.page_number,
        source_snippet=question.source_snippet,
    )


def _to_quiz_read(quiz: Quiz, questions: List[QuizQuestion]) -> QuizRead:
    return QuizRead(
        id=quiz.id,
        subject_id=quiz.subject_id,
        title=quiz.title,
        difficulty=quiz.difficulty,
        question_count=quiz.question_count,
        topic=quiz.topic,
        created_at=quiz.created_at,
        questions=[_to_question_read(q) for q in questions],
    )


@router.post("/generate", response_model=QuizRead, status_code=status.HTTP_201_CREATED)
async def generate_quiz(
    payload: QuizGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Generates a grounded MCQ quiz from the user's own subject documents."""
    subject = await require_owned_subject(db, payload.subject_id, current_user)

    try:
        quiz = await quiz_generation_service.generate_quiz(
            db,
            subject=subject,
            question_count=payload.question_count,
            difficulty=payload.difficulty.value,
            topic=payload.topic,
        )
    except NoStudyMaterialsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No indexed study materials found for this subject yet. "
                "Upload and process a PDF before generating a quiz."
            ),
        )

    questions = await _load_questions(db, quiz.id)
    return _to_quiz_read(quiz, questions)


def _validate_submission(
    answers: List[AnswerItem], questions: List[QuizQuestion]
) -> None:
    expected_ids = {q.id for q in questions}
    provided_ids = [answer.question_id for answer in answers]

    if len(provided_ids) != len(set(provided_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate answers for the same question are not allowed.",
        )
    if set(provided_ids) != expected_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission must include exactly one answer for every question in the quiz.",
        )


@router.post("/{quiz_id}/submit", response_model=QuizResultResponse)
async def submit_quiz(
    quiz_id: uuid.UUID,
    payload: QuizSubmission,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Evaluates a student's answers, persists the attempt, and returns scoring
    with explanations and sources."""
    quiz = await _load_owned_quiz(db, quiz_id, current_user)
    questions = await _load_questions(db, quiz_id)

    _validate_submission(payload.answers, questions)

    answer_index = {answer.question_id: answer for answer in payload.answers}
    results = []
    evaluations = []

    for question in questions:
        answer = answer_index[question.id]
        is_correct = question.correct_option == answer.selected_option
        results.append(
            QuestionResult(
                question_id=question.id,
                question_text=question.question_text,
                selected_option=answer.selected_option,
                correct_option=question.correct_option,
                is_correct=is_correct,
                explanation=question.explanation,
                difficulty=question.difficulty,
                source_document_id=question.document_id,
                source_document_title=question.document_title,
                source_chunk_id=question.chunk_id,
                source_page_number=question.page_number,
                source_snippet=question.source_snippet,
            )
        )
        evaluations.append(
            AnswerEvaluation(
                question_id=question.id,
                question_text=question.question_text,
                selected_answer=answer.selected_option,
                correct_answer=question.correct_option,
                is_correct=is_correct,
                difficulty=question.difficulty,
                topic=question.topic,
                time_taken=answer.time_taken,
            )
        )

    scored = score_evaluations(evaluations)

    attempt = await persist_attempt(
        db,
        user=current_user,
        quiz=quiz,
        evaluations=evaluations,
        started_at=payload.started_at,
        completed_at=None,
    )
    await db.commit()
    await db.refresh(attempt)

    return QuizResultResponse(
        quiz_id=quiz.id,
        title=quiz.title,
        attempt_id=attempt.id,
        total_questions=scored.total_questions,
        score=scored.score,
        correct_answers=scored.correct_answers,
        incorrect_answers=scored.incorrect_answers,
        percentage=scored.percentage,
        results=results,
    )


@router.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_quiz(
    quiz_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-deletes an owned quiz. Historical attempts and answers for the quiz
    are preserved, but the quiz is no longer submitted or visible."""
    quiz = await _load_owned_quiz(db, quiz_id, current_user)
    quiz.deleted_at = datetime.utcnow()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
