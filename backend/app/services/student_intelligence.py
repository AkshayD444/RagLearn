import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz
from app.models.user import User
from app.services.spaced_repetition import (
    compute_flashcard_accuracy,
    compute_flashcard_mastery,
)

EVIDENCE_TARGET = 10
UNTAGGED_TOPIC = "General"

MASTERY_LEVEL_THRESHOLDS = (
    ("mastered", 85.0),
    ("proficient", 70.0),
    ("developing", 40.0),
)


def normalize_topic(topic: Optional[str]) -> str:
    """Normalizes optional free-text topic metadata into a stable bucket label."""
    if not topic:
        return UNTAGGED_TOPIC
    cleaned = topic.strip()
    return cleaned or UNTAGGED_TOPIC


@dataclass
class AnswerEvaluation:
    """Per-question scoring snapshot produced by the quiz submission flow."""

    question_id: uuid.UUID
    question_text: str
    selected_answer: str
    correct_answer: str
    is_correct: bool
    difficulty: str
    topic: Optional[str]
    time_taken: Optional[float] = None


@dataclass
class AttemptScore:
    """Aggregate score of a single attempt, derived from its evaluations."""

    score: int
    total_questions: int
    correct_answers: int
    incorrect_answers: int
    percentage: float


def score_evaluations(evaluations: List[AnswerEvaluation]) -> AttemptScore:
    """Single source of truth for attempt scoring.

    The quiz submission route and :func:`persist_attempt` both need these
    numbers; computing them in one place keeps the stored attempt and the
    response payload from drifting apart.
    """
    total = len(evaluations)
    correct = sum(1 for evaluation in evaluations if evaluation.is_correct)
    percentage = round(correct / total * 100, 1) if total else 0.0
    return AttemptScore(
        score=correct,
        total_questions=total,
        correct_answers=correct,
        incorrect_answers=total - correct,
        percentage=percentage,
    )


def compute_accuracy(correct: int, attempted: int) -> float:
    """Percentage accuracy (0-100)."""
    if attempted <= 0:
        return 0.0
    return round(correct / attempted * 100, 1)


def compute_mastery(correct: int, attempted: int) -> float:
    """Confidence-aware 0-100 mastery score.

    accuracy = correct / attempted
    confidence grows linearly from 0 to 1 as evidence accumulates toward
    EVIDENCE_TARGET questions.
    mastery = accuracy * 100 * min(1, attempted / EVIDENCE_TARGET)
    """
    if attempted <= 0:
        return 0.0
    accuracy = correct / attempted
    confidence = min(1.0, attempted / EVIDENCE_TARGET)
    return round(accuracy * confidence * 100, 1)


def mastery_level(score: float) -> str:
    """Maps a mastery score onto a coarse, human-readable level."""
    for level, threshold in MASTERY_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "novice"


async def _select_mastery(
    db: AsyncSession, *, user_id: uuid.UUID, subject_id: uuid.UUID, topic: str
) -> Optional[TopicMastery]:
    result = await db.execute(
        select(TopicMastery).where(
            TopicMastery.user_id == user_id,
            TopicMastery.subject_id == subject_id,
            TopicMastery.topic == topic,
        )
    )
    return result.scalar_one_or_none()


def _counter_increment(
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    topic: str,
    attempted_delta: int,
    correct_delta: int,
    incorrect_delta: int,
    attempts_delta: int,
) -> Any:
    """Builds a single-statement UPDATE that adds the deltas in the database.

    The counters are incremented as column expressions rather than read into
    Python and written back, so two concurrent attempts on the same topic
    cannot lose an increment or break
    ``questions_correct + questions_incorrect == questions_attempted``.
    """
    return (
        update(TopicMastery)
        .where(
            TopicMastery.user_id == user_id,
            TopicMastery.subject_id == subject_id,
            TopicMastery.topic == topic,
        )
        .values(
            questions_attempted=TopicMastery.questions_attempted + attempted_delta,
            questions_correct=TopicMastery.questions_correct + correct_delta,
            questions_incorrect=TopicMastery.questions_incorrect + incorrect_delta,
            attempts=TopicMastery.attempts + attempts_delta,
        )
    )


async def _create_mastery_row(
    db: AsyncSession, *, user_id: uuid.UUID, subject_id: uuid.UUID, topic: str
) -> None:
    """Inserts the identity columns for a topic, tolerating a concurrent creator.

    ``uq_topic_mastery_user_subject_topic`` is the single point of truth for
    "does this row exist". A losing racer absorbs the IntegrityError inside a
    savepoint so the surrounding transaction stays usable and the caller can
    simply re-apply its deltas to the winning row.
    """
    try:
        async with db.begin_nested():
            db.add(
                TopicMastery(
                    user_id=user_id,
                    subject_id=subject_id,
                    topic=topic,
                    last_attempt_at=None,
                )
            )
            await db.flush()
    except IntegrityError:
        pass


async def _upsert_mastery(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    topic: str,
    increment: Callable[[], Any],
) -> TopicMastery:
    """Applies an arbitrary delta statement to a topic's row, creating it if needed.

    The UPDATE always runs first and is a harmless no-op when the row is not
    there yet. Only when the follow-up read proves the row genuinely does not
    exist is it created and the deltas re-applied, which closes both the
    lost-update race and the concurrent-create race.

    The caller supplies the statement so the quiz channel and the flashcard
    channel can share this lifecycle without sharing a counter.
    """
    await db.execute(increment())
    mastery = await _select_mastery(
        db, user_id=user_id, subject_id=subject_id, topic=topic
    )
    if mastery is None:
        await _create_mastery_row(
            db, user_id=user_id, subject_id=subject_id, topic=topic
        )
        await db.execute(increment())
        mastery = await _select_mastery(
            db, user_id=user_id, subject_id=subject_id, topic=topic
        )
    if mastery is None:
        # The row was just inserted or is visible to this transaction, so its
        # absence here means the unique key could not be established at all.
        # Fail loudly instead of letting the caller dereference None.
        raise RuntimeError(
            f"Could not resolve topic mastery for subject {subject_id} "
            f"and topic {topic!r}."
        )
    return mastery


async def _apply_mastery_counters(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    topic: str,
    attempted_delta: int = 0,
    correct_delta: int = 0,
    incorrect_delta: int = 0,
    attempts_delta: int = 0,
) -> TopicMastery:
    """Atomically adds the deltas to a topic's quiz counters and returns the row."""

    def increment() -> Any:
        return _counter_increment(
            user_id=user_id,
            subject_id=subject_id,
            topic=topic,
            attempted_delta=attempted_delta,
            correct_delta=correct_delta,
            incorrect_delta=incorrect_delta,
            attempts_delta=attempts_delta,
        )

    return await _upsert_mastery(
        db,
        user_id=user_id,
        subject_id=subject_id,
        topic=topic,
        increment=increment,
    )


async def _refresh_mastery(mastery: TopicMastery, last_attempt_at: datetime) -> None:
    """Recomputes the derived mastery fields from the stored counters.

    Kept as a pure function of ``questions_correct``/``questions_attempted`` so
    it is safe to re-run: a racing writer can only ever lag by one increment,
    never corrupt the counters themselves.
    """
    mastery.accuracy = compute_accuracy(
        mastery.questions_correct, mastery.questions_attempted
    )
    mastery.mastery_score = compute_mastery(
        mastery.questions_correct, mastery.questions_attempted
    )
    mastery.mastery_level = mastery_level(mastery.mastery_score)
    mastery.last_attempt_at = last_attempt_at
    mastery.updated_at = datetime.utcnow()


def _flashcard_counter_increment(
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    topic: str,
    reviewed_delta: int,
    correct_delta: int,
) -> Any:
    """Builds a single-statement UPDATE for the flashcard evidence channel.

    Mirrors :func:`_counter_increment` for the same reason: the counters are
    incremented as column expressions in the database so two concurrent reviews
    of the same topic cannot lose an increment.
    """
    return (
        update(TopicMastery)
        .where(
            TopicMastery.user_id == user_id,
            TopicMastery.subject_id == subject_id,
            TopicMastery.topic == topic,
        )
        .values(
            flashcard_reviews=TopicMastery.flashcard_reviews + reviewed_delta,
            flashcard_correct=TopicMastery.flashcard_correct + correct_delta,
        )
    )


async def apply_flashcard_counters(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    topic: str,
    reviewed_delta: int = 0,
    correct_delta: int = 0,
) -> TopicMastery:
    """Records self-graded flashcard reviews against a topic.

    Writes only the ``flashcard_*`` columns. The quiz channel -- and therefore
    every weak-topic rule, recommendation, and study-plan priority -- is left
    exactly as it was, so flashcard practice informs the flashcard progress
    panel without silently re-ranking a student's whole study plan.
    """

    def increment() -> Any:
        return _flashcard_counter_increment(
            user_id=user_id,
            subject_id=subject_id,
            topic=topic,
            reviewed_delta=reviewed_delta,
            correct_delta=correct_delta,
        )

    mastery = await _upsert_mastery(
        db,
        user_id=user_id,
        subject_id=subject_id,
        topic=topic,
        increment=increment,
    )
    mastery.flashcard_accuracy = compute_flashcard_accuracy(
        mastery.flashcard_correct, mastery.flashcard_reviews
    )
    mastery.flashcard_mastery = compute_flashcard_mastery(
        mastery.flashcard_correct, mastery.flashcard_reviews
    )
    mastery.updated_at = datetime.utcnow()
    return mastery


async def persist_attempt(
    db: AsyncSession,
    *,
    user: User,
    quiz: Quiz,
    evaluations: List[AnswerEvaluation],
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
) -> QuizAttempt:
    """Stores a scored attempt, its per-question snapshots, and updates topic mastery.

    The attempt inherits the quiz owner (user.id) so attempts can never be
    attached to a different authenticated user.
    """
    result = score_evaluations(evaluations)

    resolved_completed_at = completed_at or datetime.utcnow()
    attempt = QuizAttempt(
        user_id=user.id,
        quiz_id=quiz.id,
        subject_id=quiz.subject_id,
        score=result.score,
        total_questions=result.total_questions,
        percentage=result.percentage,
        started_at=started_at,
        completed_at=resolved_completed_at,
    )
    db.add(attempt)
    await db.flush()

    seen_topics = set()
    for evaluation in evaluations:
        topic = normalize_topic(evaluation.topic)
        db.add(
            QuizAttemptAnswer(
                attempt_id=attempt.id,
                question_id=evaluation.question_id,
                question_text=evaluation.question_text,
                selected_answer=evaluation.selected_answer,
                correct_answer=evaluation.correct_answer,
                is_correct=evaluation.is_correct,
                difficulty=evaluation.difficulty,
                topic=topic,
                time_taken=evaluation.time_taken,
            )
        )

        first_answer_for_topic = topic not in seen_topics
        mastery = await _apply_mastery_counters(
            db,
            user_id=user.id,
            subject_id=quiz.subject_id,
            topic=topic,
            attempted_delta=1,
            correct_delta=1 if evaluation.is_correct else 0,
            incorrect_delta=0 if evaluation.is_correct else 1,
            attempts_delta=1 if first_answer_for_topic else 0,
        )
        seen_topics.add(topic)
        await _refresh_mastery(mastery, resolved_completed_at)

    return attempt
