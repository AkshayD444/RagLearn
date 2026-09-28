"""Deterministic next-learning recommendation service.

Answers the one question a student actually sees on the dashboard: *what should
I study next?* Every other engine in the app answers a scoped version of it --
weak topics, per-subject recommendations, study plans. This module is the
single, cross-subject synthesis: it picks the topic with the highest adaptive
priority and recommends one of the three learning activities the application
can already deliver (``study``, ``quiz``, ``flashcards``).

It is a read-side, deterministic engine. There are no writes, no randomness,
and no LLM involvement, and every threshold is a documented constant.

Selection
---------
1. Load every topic's metrics and its full answer history, then estimate
   confidence-gated mastery with
   :func:`app.services.adaptive_mastery.compute_adaptive_mastery`, which folds
   in question difficulty, answer recency, repeated attempts, and the flashcard
   evidence channel.
2. Score each candidate with :func:`app.services.adaptive_learning.extended_priority_score`,
   which ranks mastery, weakness, error, recency, and difficulty.
3. Pick the highest-priority candidate (deterministic tie-breaks: lower
   mastery, more attempts, more recent activity, subject title, topic).
4. Choose the activity for that single winner from deterministic rules.

Activity rules (checked in order)
---------------------------------
``flashcards``
    The winner has due flashcards now. A due card is a scheduled, time-sensitive
    review, so it outranks a generic practice suggestion.
``study``
    No indexed study material for the subject, or fewer than
    ``MIN_REVIEW_EVIDENCE`` questions answered -- it is too early to quiz and
    there is nothing to read, so the recommendation is to enable that.
``quiz``
    Otherwise. A low-accuracy topic gets a practice quiz; a proficient topic
    gets a maintenance quiz. `"quiz"` is the default way the app turns practice
    into graded evidence, so every other state maps to it rather than to a
    capability the product does not have.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.services.adaptive_learning import (
    MIN_REVIEW_EVIDENCE,
    PROFICIENT_MASTERY,
    REVIEW_ACCURACY_THRESHOLD,
    TopicMetrics,
    extended_priority_score,
    load_metrics,
    suggested_difficulty,
)
from app.services.adaptive_mastery import (
    MasteryEstimate,
    ScoredAnswer,
    compute_adaptive_mastery,
)
from app.services.flashcard_schedule import due_counts_by_subject_topic
from app.services.study_planner import has_indexed_material

#: Activity types. These are the three things the application can already do,
#: so a recommendation always maps onto a real action rather than a wish.
STUDY = "study"
QUIZ = "quiz"
FLASHCARDS = "flashcards"

#: Fixed reason returned by the API when there is nothing to recommend.
INSUFFICIENT_DATA_REASON = (
    "Insufficient learning history to recommend a next topic; "
    "answer a few questions first."
)


@dataclass(frozen=True)
class NextLearningRecommendation:
    """The single best next step, with everything needed to render a decision."""

    subject_id: uuid.UUID
    subject_title: str
    topic: str
    priority: float
    mastery: float
    mastery_level: str
    accuracy: float
    weighted_accuracy: float
    difficulty_factor: float
    questions_attempted: int
    activity_type: str
    reason: str
    suggested_difficulty: str
    due_cards: int
    last_attempt_at: Optional[datetime]


@dataclass
class _Candidate:
    metrics: TopicMetrics
    estimate: MasteryEstimate
    material_available: bool
    due_cards: int
    priority: float


def select_activity(
    *,
    estimate: MasteryEstimate,
    metrics: TopicMetrics,
    material_available: bool,
    due_cards: int,
) -> Tuple[str, str]:
    """The deterministic activity + reason for one topic's measured state."""
    if due_cards > 0:
        return (
            FLASHCARDS,
            f"{due_cards} flashcard{'s' if due_cards != 1 else ''} are due for this topic.",
        )
    if not material_available:
        return (
            STUDY,
            "Indexed study material is unavailable; upload a document before practicing this topic.",
        )
    if metrics.questions_attempted < MIN_REVIEW_EVIDENCE:
        return (
            STUDY,
            "Not enough questions yet -- study the material on this topic first.",
        )
    if metrics.accuracy < REVIEW_ACCURACY_THRESHOLD:
        return (
            QUIZ,
            f"Low accuracy ({metrics.accuracy}%) -- practice this topic with a quiz.",
        )
    if estimate.mastery >= PROFICIENT_MASTERY:
        return (
            QUIZ,
            f"Strong mastery ({estimate.mastery}%) -- keep it sharp with a practice quiz.",
        )
    return (
        QUIZ,
        f"Partial understanding ({metrics.accuracy}%) -- more practice questions will help.",
    )


def _tie_break_time(value: Optional[datetime]) -> float:
    return value.timestamp() if value else 0.0


def rank_candidates(
    candidates: Sequence[_Candidate],
) -> List[_Candidate]:
    """Deterministic ordering: highest priority first, then measurable stability.

    Ties fall through poorer mastery, more attempts, more recent activity,
    subject title, then topic, so the output is reproducible even when two
    topics score identically.
    """
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.priority,
            candidate.estimate.mastery,
            -candidate.metrics.attempts,
            -_tie_break_time(candidate.metrics.last_attempt_at),
            candidate.metrics.subject_title,
            candidate.metrics.topic,
        ),
    )


async def _load_scored_answers(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_ids: Sequence[uuid.UUID],
    subject_id: Optional[uuid.UUID],
) -> Dict[Tuple[uuid.UUID, str], List[ScoredAnswer]]:
    """The full graded answer history per (subject, topic) for the candidates.

    Scoped by ``user_id`` and by the active subjects a caller already narrowed
    down to; with ``subject_id`` set the query is narrowed further, matching the
    ownership rules the API layer applies.
    """
    filters = [QuizAttempt.user_id == user_id]
    if subject_id is not None:
        filters.append(QuizAttempt.subject_id == subject_id)
    elif subject_ids:
        filters.append(QuizAttempt.subject_id.in_(list(subject_ids)))

    result = await db.execute(
        select(QuizAttemptAnswer, QuizAttempt.subject_id)
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
        .where(*filters)
        .order_by(QuizAttemptAnswer.created_at.asc())
    )

    by_key: Dict[Tuple[uuid.UUID, str], List[ScoredAnswer]] = defaultdict(list)
    for answer, answer_subject_id in result.all():
        by_key[(answer_subject_id, answer.topic)].append(
            ScoredAnswer(
                difficulty=answer.difficulty,
                is_correct=answer.is_correct,
                answered_at=answer.created_at,
            )
        )
    return dict(by_key)


async def recommend_next_learning(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: Optional[uuid.UUID] = None,
    now: Optional[datetime] = None,
) -> Optional[NextLearningRecommendation]:
    """The best next step across the user's active subjects, or ``None``.

    ``None`` means there is no evidence at all (no graded answers anywhere and
    no flashcard reviews), which the API reports as ``insufficient_data``.
    Deleted subjects are excluded because :func:`load_metrics` already scopes
    to active subjects owned by ``user_id``.
    """
    resolved_now = now or datetime.utcnow()

    metrics = await load_metrics(db, user_id, subject_id=subject_id)
    if not metrics:
        return None

    subject_ids = sorted({item.subject_id for item in metrics})
    answers_by_key = await _load_scored_answers(
        db, user_id=user_id, subject_ids=subject_ids, subject_id=subject_id
    )
    due_by_key = await due_counts_by_subject_topic(
        db, user_id=user_id, subject_ids=subject_ids, now=resolved_now
    )

    candidates: List[_Candidate] = []
    material_by_subject: Dict[uuid.UUID, bool] = {}
    for item in metrics:
        estimate = compute_adaptive_mastery(
            answers=answers_by_key.get((item.subject_id, item.topic), []),
            flashcard_reviews=item.flashcard_reviews,
            flashcard_correct=item.flashcard_correct,
            now=resolved_now,
        )
        if estimate is None:
            continue
        if item.subject_id not in material_by_subject:
            material_by_subject[item.subject_id] = await has_indexed_material(
                db, user_id, item.subject_id
            )
        priority = extended_priority_score(
            item,
            difficulty_index=estimate.difficulty_factor,
            mastery_ratio=estimate.mastery / 100.0,
        )
        candidates.append(
            _Candidate(
                metrics=item,
                estimate=estimate,
                material_available=material_by_subject[item.subject_id],
                due_cards=due_by_key.get((item.subject_id, item.topic), 0),
                priority=priority,
            )
        )

    if not candidates:
        return None

    winner = rank_candidates(candidates)[0]
    activity_type, reason = select_activity(
        estimate=winner.estimate,
        metrics=winner.metrics,
        material_available=winner.material_available,
        due_cards=winner.due_cards,
    )

    return NextLearningRecommendation(
        subject_id=winner.metrics.subject_id,
        subject_title=winner.metrics.subject_title,
        topic=winner.metrics.topic,
        priority=winner.priority,
        mastery=winner.estimate.mastery,
        mastery_level=winner.estimate.level,
        accuracy=winner.metrics.accuracy,
        weighted_accuracy=winner.estimate.weighted_accuracy,
        difficulty_factor=winner.estimate.difficulty_factor,
        questions_attempted=winner.metrics.questions_attempted,
        activity_type=activity_type,
        reason=reason,
        suggested_difficulty=suggested_difficulty(winner.estimate.mastery),
        due_cards=winner.due_cards,
        last_attempt_at=winner.metrics.last_attempt_at,
    )