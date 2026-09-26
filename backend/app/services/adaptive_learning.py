"""Deterministic Adaptive Learning Engine.

Identifies which of a student's topics deserve attention next, using only
existing measured data (topic mastery, quiz attempts, per-question answers,
accuracy, and recency). It is intentionally rule-based and explainable: no
machine-learning models and no LLM involvement.

Priority formula
----------------
For every topic with at least one answered question a score in 0-100 is
computed from four measurable signals:

    priority = evidence_gate * (
        w_weakness * weakness
      + w_error    * error
      + w_recency  * recency
    ) * 100

  * evidence_gate = min(1, questions_attempted / EVIDENCE_TARGET)
    Confidence gate: a topic with very little evidence can never dominate
    recommendations, no matter how weak it looks.

  * weakness = 1 - accuracy            (accuracy = correct / attempted)
    Lower accuracy -> higher priority.

  * error = error_rate * 0.70 + repetition * 0.30
    error_rate = incorrect / attempted; repetition = min(1, incorrect / 3)
    Repeated mistakes push priority up and saturate once several have piled up.

  * recency = importance * (1 - recent_accuracy)
    importance = 1 / (1 + days_since_last_attempt / 7)
    Recent weak performance raises priority; a strong recent result shrinks it,
    which avoids recommending effortless repetition of already-hot topics.
    When either input is unknown the whole term drops out, so missing data is
    never scored as weak recent performance.

Weights are constants chosen so the total stays within 0-100 after evidence
gating: w_weakness = 0.45, w_error = 0.35, w_recency = 0.20.

Determinism
-----------
Recommendations are sorted by priority (desc), then mastery (asc), attempts
(desc), last attempt time (desc), subject title, and topic, giving a
reproducible ordering with no randomness.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.subject import Subject
from app.services.student_intelligence import EVIDENCE_TARGET

# Evidence and repetition thresholds
REPEAT_EVIDENCE = 3
MIN_REVIEW_EVIDENCE = 3
RECENT_WINDOW = 5
RECENCY_HALF_LIFE_DAYS = 7.0

# Priority formula components
W_WEAKNESS = 0.45
W_ERROR = 0.35
W_RECENCY = 0.20
ERROR_REPETITION_BOOST = 0.30

# Recommendation type boundaries
REVIEW_ACCURACY_THRESHOLD = 60.0
MIN_MAINTAIN_ACCURACY = 80.0
WEAK_MASTERY = 40.0
PROFICIENT_MASTERY = 70.0

# Recommendation types
REVIEW = "REVIEW"
PRACTICE = "PRACTICE"
MAINTAIN = "MAINTAIN"

SUGGESTED_ACTIONS = {
    REVIEW: "Review this topic, then retake a short practice quiz.",
    PRACTICE: "Take more practice questions on this topic.",
    MAINTAIN: "Do an occasional practice quiz to keep this topic sharp.",
}

TopicKey = Tuple[uuid.UUID, str]


@dataclass
class TopicMetrics:
    """Rolled-up, per-topic state used by the priority formula."""

    subject_id: uuid.UUID
    subject_title: str
    topic: str
    attempts: int
    questions_attempted: int
    questions_correct: int
    questions_incorrect: int
    accuracy: float
    mastery_score: float
    mastery_level: str
    last_attempt_at: Optional[datetime]
    recent_attempted: int = 0
    recent_correct: int = 0


@dataclass
class Recommendation:
    """A single actionable recommendation for the student."""

    subject_id: uuid.UUID
    subject_title: str
    topic: str
    priority: float
    mastery: float
    mastery_level: str
    accuracy: float
    attempts: int
    questions_attempted: int
    questions_incorrect: int
    recommendation_type: str
    reason: str
    suggested_action: str
    suggested_difficulty: str
    last_attempt_at: Optional[datetime]


def _as_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalizes aware datetimes to naive UTC for safe arithmetic."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def days_since(value: Optional[datetime], now: datetime) -> Optional[float]:
    """Whole/partial days between a stored timestamp and now."""
    value = _as_naive_utc(value)
    if value is None:
        return None
    delta = now - value
    if delta.total_seconds() < 0:
        delta = timedelta(0)
    return delta.total_seconds() / 86400.0


def weakness_component(accuracy: float) -> float:
    return max(0.0, min(1.0, 1.0 - accuracy))


def error_component(incorrect: int, attempted: int) -> float:
    error_rate = incorrect / max(1, attempted)
    repetition = min(1.0, incorrect / REPEAT_EVIDENCE)
    return (
        error_rate * (1.0 - ERROR_REPETITION_BOOST)
        + repetition * ERROR_REPETITION_BOOST
    )


def recency_component(
    days: Optional[float], recent_accuracy: Optional[float]
) -> float:
    """Recency contribution, or 0.0 when either input is unknown.

    A missing ``days`` and a missing ``recent_accuracy`` both mean "not
    measured", and neither may be read as evidence of weak recent
    performance. Substituting 0.0 for an unknown accuracy would make
    ``importance * (1 - 0) == importance``, i.e. it would hand the largest
    possible recency boost to exactly those topics that have no recent
    answers at all.
    """
    if days is None or recent_accuracy is None:
        return 0.0
    importance = 1.0 / (1.0 + days / RECENCY_HALF_LIFE_DAYS)
    return importance * (1.0 - recent_accuracy)


def evidence_gate(attempted: int) -> float:
    return min(1.0, attempted / EVIDENCE_TARGET)


def priority_score(metrics: TopicMetrics) -> float:
    """0-100 deterministic priority for a single topic."""
    accuracy = metrics.questions_correct / max(1, metrics.questions_attempted)
    recent_accuracy = (
        metrics.recent_correct / metrics.recent_attempted
        if metrics.recent_attempted
        else None
    )
    blended = (
        W_WEAKNESS * weakness_component(accuracy)
        + W_ERROR * error_component(
            metrics.questions_incorrect, metrics.questions_attempted
        )
        + W_RECENCY * recency_component(
            days_since(metrics.last_attempt_at, datetime.utcnow()),
            recent_accuracy,
        )
    )
    return round(blended * evidence_gate(metrics.questions_attempted) * 100.0, 1)


def recommendation_type_and_reason(metrics: TopicMetrics) -> Tuple[str, str]:
    """Maps measured state onto REVIEW / PRACTICE / MAINTAIN and a data-backed reason."""
    accuracy_pct = metrics.accuracy
    attempted = metrics.questions_attempted
    incorrect = metrics.questions_incorrect
    overall_accuracy = metrics.questions_correct / max(1, attempted)

    recent_accuracy = (
        metrics.recent_correct / metrics.recent_attempted
        if metrics.recent_attempted
        else None
    )

    repeated = incorrect >= REPEAT_EVIDENCE
    low_accuracy = (
        accuracy_pct < REVIEW_ACCURACY_THRESHOLD
        and attempted >= MIN_REVIEW_EVIDENCE
    )
    # Judged on accuracy, not on ``mastery_score``: the stored mastery is scaled
    # by the confidence gate (mastery = accuracy * min(1, attempted /
    # EVIDENCE_TARGET) * 100), so a short but flawless history scores below
    # WEAK_MASTERY and would otherwise be reported as needing review.
    low_ability_with_attempts = accuracy_pct < WEAK_MASTERY and metrics.attempts >= 2
    recent_decline = (
        metrics.recent_attempted >= 2
        and recent_accuracy is not None
        and recent_accuracy < overall_accuracy - 0.05
    )

    if low_accuracy or repeated or low_ability_with_attempts:
        if recent_decline:
            return REVIEW, "Recent performance has declined."
        if repeated:
            return REVIEW, "Repeated incorrect answers on this topic."
        if low_accuracy:
            return REVIEW, f"Low accuracy ({accuracy_pct}%) across {attempted} questions."
        return REVIEW, "Low accuracy on this topic despite multiple practice attempts."

    if accuracy_pct >= MIN_MAINTAIN_ACCURACY:
        return MAINTAIN, f"Strong accuracy ({accuracy_pct}%) across {attempted} questions."

    if attempted < MIN_REVIEW_EVIDENCE:
        return PRACTICE, "Not enough questions yet — more practice will clarify this topic."
    if accuracy_pct >= REVIEW_ACCURACY_THRESHOLD:
        return (
            PRACTICE,
            f"Partial understanding ({accuracy_pct}% accuracy) across {attempted} questions.",
        )
    return PRACTICE, f"Accuracy is low ({accuracy_pct}%) — more practice is recommended."


def suggested_difficulty(mastery_score: float) -> str:
    if mastery_score < WEAK_MASTERY:
        return "easy"
    if mastery_score < PROFICIENT_MASTERY:
        return "medium"
    return "hard"


def build_recommendations(metrics: List[TopicMetrics]) -> List[Recommendation]:
    """Builds and deterministically sorts recommendations from topic metrics."""
    recommendations: List[Recommendation] = []
    for item in metrics:
        if item.questions_attempted <= 0:
            continue
        recommendation_type, reason = recommendation_type_and_reason(item)
        recommendations.append(
            Recommendation(
                subject_id=item.subject_id,
                subject_title=item.subject_title,
                topic=item.topic,
                priority=priority_score(item),
                mastery=item.mastery_score,
                mastery_level=item.mastery_level,
                accuracy=item.accuracy,
                attempts=item.attempts,
                questions_attempted=item.questions_attempted,
                questions_incorrect=item.questions_incorrect,
                recommendation_type=recommendation_type,
                reason=reason,
                suggested_action=SUGGESTED_ACTIONS[recommendation_type],
                suggested_difficulty=suggested_difficulty(item.mastery_score),
                last_attempt_at=item.last_attempt_at,
            )
        )

    recommendations.sort(
        key=lambda r: (
            -r.priority,
            r.mastery,
            -r.attempts,
            -(r.last_attempt_at.timestamp() if r.last_attempt_at else 0.0),
            r.subject_title,
            r.topic,
        )
    )
    return recommendations


async def load_metrics(
    db: AsyncSession,
    user_id: uuid.UUID,
    subject_id: Optional[uuid.UUID] = None,
) -> List[TopicMetrics]:
    """Loads mastery and recent-answer metrics for the user's active subjects.

    Only rows belonging to the requesting user and to non-deleted subjects are
    returned, matching the ownership rules enforced by the API layer.
    """
    subject_conditions = [
        Subject.user_id == user_id,
        Subject.deleted_at.is_(None),
    ]
    mastery_conditions = [TopicMastery.user_id == user_id]
    if subject_id is not None:
        subject_conditions.append(Subject.id == subject_id)
        mastery_conditions.append(TopicMastery.subject_id == subject_id)

    result = await db.execute(
        select(TopicMastery, Subject.title)
        .join(Subject, Subject.id == TopicMastery.subject_id)
        .where(*subject_conditions, *mastery_conditions)
    )

    metrics_by_key: Dict[TopicKey, TopicMetrics] = {}
    active_subject_ids = set()
    for mastery, subject_title in result.all():
        active_subject_ids.add(mastery.subject_id)
        metrics_by_key[(mastery.subject_id, mastery.topic)] = TopicMetrics(
            subject_id=mastery.subject_id,
            subject_title=subject_title,
            topic=mastery.topic,
            attempts=mastery.attempts,
            questions_attempted=mastery.questions_attempted,
            questions_correct=mastery.questions_correct,
            questions_incorrect=mastery.questions_incorrect,
            accuracy=mastery.accuracy,
            mastery_score=mastery.mastery_score,
            mastery_level=mastery.mastery_level,
            last_attempt_at=mastery.last_attempt_at,
        )

    if active_subject_ids:
        answers_result = await db.execute(
            select(QuizAttemptAnswer, QuizAttempt.subject_id)
            .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
            .where(
                QuizAttempt.user_id == user_id,
                QuizAttempt.subject_id.in_(active_subject_ids),
            )
            .order_by(QuizAttemptAnswer.created_at.asc())
        )
        recent_by_key: Dict[TopicKey, List[QuizAttemptAnswer]] = defaultdict(list)
        for answer, answer_subject_id in answers_result.all():
            recent_by_key[(answer_subject_id, answer.topic)].append(answer)

        for key, answers in recent_by_key.items():
            metrics = metrics_by_key.get(key)
            if metrics is None:
                continue
            recent = answers[-RECENT_WINDOW:]
            metrics.recent_attempted = len(recent)
            metrics.recent_correct = sum(1 for answer in recent if answer.is_correct)

    return list(metrics_by_key.values())