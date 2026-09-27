import uuid
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user, require_owned_subject
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.quiz import Quiz, QuizQuestion
from app.models.subject import Subject
from app.models.user import User
from app.schemas.pagination import Page
from app.schemas.quiz import (
    AnswerItem,
    QuestionResult,
    QuizAttemptAnswerRead,
    QuizAttemptDetail,
    QuizAttemptSummary,
    QuizDetail,
    QuizGenerateRequest,
    QuizQuestionRead,
    QuizRead,
    QuizResultResponse,
    QuizSubmission,
    QuizSummary,
)
from app.services.quiz_engine import NoStudyMaterialsError, quiz_generation_service
from app.services.student_intelligence import (
    AnswerEvaluation,
    persist_attempt,
    score_evaluations,
)

router = APIRouter(prefix="/quizzes", tags=["Quizzes"])

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

# Submissions are validated to answer every question and a quiz is capped at 20
# questions, so an attempt can never hold more rows than this. The bound exists
# so a corrupt row can never turn an attempt detail into an unbounded read.
MAX_ATTEMPT_ANSWERS = 200


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


# ---------------------------------------------------------------------------
# History
#
# Every query below is filtered on the authenticated user's id, and every list is
# a bounded page over an explicit total. Static paths are declared before
# "/{quiz_id}" so they are not swallowed by the UUID path parameter.
# ---------------------------------------------------------------------------


class _QuizStats(NamedTuple):
    attempt_count: int
    best_percentage: float
    last_attempt_at: Optional[datetime]


async def _quiz_stats_for(
    db: AsyncSession, user_id: uuid.UUID, quiz_ids: List[uuid.UUID]
) -> Dict[uuid.UUID, _QuizStats]:
    """Aggregate attempt figures for one page of quizzes.

    Bounded by the page size rather than the history length, so a student with
    thousands of attempts never materializes them to label a list of 20.
    """

    stats: Dict[uuid.UUID, _QuizStats] = {
        quiz_id: _QuizStats(0, 0.0, None) for quiz_id in quiz_ids
    }
    if not quiz_ids:
        return stats

    rows = await db.execute(
        select(
            QuizAttempt.quiz_id,
            func.count(QuizAttempt.id),
            func.max(QuizAttempt.percentage),
            func.max(QuizAttempt.completed_at),
        )
        .where(QuizAttempt.user_id == user_id, QuizAttempt.quiz_id.in_(quiz_ids))
        .group_by(QuizAttempt.quiz_id)
    )
    for quiz_id, attempt_count, best_percentage, last_attempt_at in rows.all():
        stats[quiz_id] = _QuizStats(
            attempt_count=int(attempt_count or 0),
            best_percentage=round(float(best_percentage or 0.0), 1),
            last_attempt_at=last_attempt_at,
        )
    return stats


def _to_quiz_summary(quiz: Quiz, subject_title: Optional[str], stats: _QuizStats) -> QuizSummary:
    return QuizSummary(
        id=quiz.id,
        subject_id=quiz.subject_id,
        subject_title=subject_title,
        title=quiz.title,
        difficulty=quiz.difficulty,
        question_count=quiz.question_count,
        topic=quiz.topic,
        created_at=quiz.created_at,
        attempt_count=stats.attempt_count,
        best_percentage=stats.best_percentage,
        last_attempt_at=stats.last_attempt_at,
    )


def _to_attempt_summary(
    attempt: QuizAttempt, quiz_title: str, subject_title: str
) -> QuizAttemptSummary:
    return QuizAttemptSummary(
        id=attempt.id,
        quiz_id=attempt.quiz_id,
        subject_id=attempt.subject_id,
        quiz_title=quiz_title,
        subject_title=subject_title,
        score=attempt.score,
        total_questions=attempt.total_questions,
        percentage=attempt.percentage,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
    )


@router.get("", response_model=Page[QuizSummary])
async def list_quizzes(
    subject_id: Optional[uuid.UUID] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Owned quiz history, newest first, with attempt aggregates per quiz."""
    filters = [Quiz.user_id == current_user.id, Quiz.deleted_at.is_(None)]
    if subject_id is not None:
        await require_owned_subject(db, subject_id, current_user)
        filters.append(Quiz.subject_id == subject_id)

    total = await db.scalar(select(func.count(Quiz.id)).where(*filters))
    rows = await db.execute(
        select(Quiz, Subject.title)
        .outerjoin(Subject, Subject.id == Quiz.subject_id)
        .where(*filters)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        .limit(limit)
        .offset(offset)
    )
    page = [(quiz, subject_title) for quiz, subject_title in rows.all()]
    stats = await _quiz_stats_for(db, current_user.id, [quiz.id for quiz, _ in page])

    return Page[QuizSummary](
        items=[
            _to_quiz_summary(quiz, subject_title, stats[quiz.id])
            for quiz, subject_title in page
        ],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@router.get("/attempts", response_model=Page[QuizAttemptSummary])
async def list_quiz_attempts(
    subject_id: Optional[uuid.UUID] = None,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Owned attempt history, most recent first, with quiz and subject titles
    joined in. Survives quiz deletion so past work stays reviewable."""
    filters = [QuizAttempt.user_id == current_user.id]
    if subject_id is not None:
        await require_owned_subject(db, subject_id, current_user)
        filters.append(QuizAttempt.subject_id == subject_id)

    total = await db.scalar(select(func.count(QuizAttempt.id)).where(*filters))
    rows = await db.execute(
        select(QuizAttempt, Quiz.title, Subject.title)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .join(Subject, Subject.id == QuizAttempt.subject_id)
        .where(*filters)
        .order_by(QuizAttempt.completed_at.desc(), QuizAttempt.id.desc())
        .limit(limit)
        .offset(offset)
    )

    return Page[QuizAttemptSummary](
        items=[
            _to_attempt_summary(attempt, quiz_title, subject_title)
            for attempt, quiz_title, subject_title in rows.all()
        ],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@router.get("/attempts/{attempt_id}", response_model=QuizAttemptDetail)
async def get_quiz_attempt(
    attempt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """One owned attempt with every answer it recorded."""
    row = await db.execute(
        select(QuizAttempt, Quiz.title, Subject.title)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .join(Subject, Subject.id == QuizAttempt.subject_id)
        .where(QuizAttempt.id == attempt_id, QuizAttempt.user_id == current_user.id)
    )
    found = row.first()
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attempt not found or access denied.",
        )
    attempt, quiz_title, subject_title = found

    answers = await db.execute(
        select(QuizAttemptAnswer)
        .where(QuizAttemptAnswer.attempt_id == attempt.id)
        .order_by(QuizAttemptAnswer.created_at, QuizAttemptAnswer.id)
        .limit(MAX_ATTEMPT_ANSWERS)
    )

    detail = _to_attempt_summary(attempt, quiz_title, subject_title)
    return QuizAttemptDetail(
        **detail.model_dump(),
        answers=[QuizAttemptAnswerRead.model_validate(a) for a in answers.scalars().all()],
    )


@router.get("/{quiz_id}", response_model=QuizDetail)
async def get_quiz(
    quiz_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """One owned, non-deleted quiz with its questions and attempt aggregates."""
    quiz = await _load_owned_quiz(db, quiz_id, current_user)
    questions = await _load_questions(db, quiz.id)
    stats = (await _quiz_stats_for(db, current_user.id, [quiz.id]))[quiz.id]

    return QuizDetail(
        id=quiz.id,
        subject_id=quiz.subject_id,
        title=quiz.title,
        difficulty=quiz.difficulty,
        question_count=quiz.question_count,
        topic=quiz.topic,
        created_at=quiz.created_at,
        questions=[_to_question_read(question) for question in questions],
        attempt_count=stats.attempt_count,
        best_percentage=stats.best_percentage,
        last_attempt_at=stats.last_attempt_at,
    )


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
