"""Deterministic weak-topic detection.

Answers a single question from recorded performance: *which topics need more
practice?* Every judgement is an explicit, inspectable rule applied to the
counters already stored on ``TopicMastery``. There is no scoring model, no
weighted ranking and no LLM involvement, so a caller can always explain why a
topic was flagged.

Rules
-----
A topic is reported as needing practice when **any** rule fires. Each rule is
independent and cites the numbers that triggered it.

``repeated_mistakes``
    ``questions_incorrect >= REPEATED_MISTAKE_COUNT``. The student has missed
    the same topic repeatedly, which is a stronger signal than one bad quiz.

``low_accuracy``
    ``questions_attempted >= MIN_ACCURACY_EVIDENCE`` **and**
    ``accuracy < LOW_ACCURACY_THRESHOLD``. Enough questions have been answered
    for the accuracy figure to mean something, and it is below target.

``recent_poor_performance``
    ``recent_attempted >= MIN_RECENT_EVIDENCE`` **and**
    ``recent_accuracy <= RECENT_POOR_ACCURACY_THRESHOLD`` **and**
    ``recent_accuracy <= accuracy - RECENT_DECLINE_MARGIN``. The most recent
    answers were both poor *and* clearly worse than the topic's overall
    accuracy, so the numbers describe a genuine decline rather than a topic
    that has always been weak. The margin rule is what keeps a uniformly-weak
    topic (recent accuracy equals overall accuracy) from firing a second,
    redundant reason.

``insufficient_evidence``
    ``questions_attempted < MIN_ACCURACY_EVIDENCE``. Too few questions to judge
    accuracy at all. Flagged so the topic is not silently mistaken for a strong
    one; more practice is what resolves it.

A topic is only ever reported when it has at least one recorded answer, and a
topic that trips no rule is not a weak topic at all, so it is excluded.

Each topic also carries a ``confidence`` label. A rule based on aggregate
counting (``repeated_mistakes``, ``low_accuracy``) is ``confirmed``; a
symptom-based or thin-evidence rule (``recent_poor_performance``,
``insufficient_evidence``) is ``low``. The label never determines whether a
topic is flagged -- only how much weight the flag should carry downstream.

Determinism
-----------
Topics are ordered alphabetically and each topic's reasons follow the fixed
``REASON_ORDER``. There is no priority score, so the output is a stable,
reproducible set rather than a ranked list.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.services.adaptive_learning import RECENT_WINDOW

# Accuracy target: a topic below this percentage needs practice.
LOW_ACCURACY_THRESHOLD = 60.0

# Questions needed before accuracy is treated as meaningful.
MIN_ACCURACY_EVIDENCE = 3

# Incorrect answers on one topic that count as a repeated mistake.
REPEATED_MISTAKE_COUNT = 3

# Answers needed in the recent window before a decline is worth reporting.
MIN_RECENT_EVIDENCE = 2

# Recent accuracy at or below this percentage is "poor" in its own right.
RECENT_POOR_ACCURACY_THRESHOLD = 50.0

# A recent window only counts as a decline when it is this many percentage
# points below the topic's overall accuracy.
RECENT_DECLINE_MARGIN = 20.0

# Stable ordering for the reasons attached to a topic.
REASON_ORDER = (
    "repeated_mistakes",
    "low_accuracy",
    "recent_poor_performance",
    "insufficient_evidence",
)

REPEATED_MISTAKES = "repeated_mistakes"
LOW_ACCURACY = "low_accuracy"
RECENT_POOR_PERFORMANCE = "recent_poor_performance"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

#: Reasons whose trigger numbers add up across the whole history are treated
#: as confirmed signs of weakness; symptom/thin-evidence reasons are not.
_CONFIRMED_CLASSES = (REPEATED_MISTAKES, LOW_ACCURACY)


@dataclass(frozen=True)
class WeakReason:
    """One triggered rule, with the numbers that triggered it."""

    code: str
    message: str


@dataclass
class WeakTopic:
    """A topic that needs more practice, plus every rule that fired for it."""

    subject_id: uuid.UUID
    topic: str
    mastery_score: float
    mastery_level: str
    accuracy: float
    attempts: int
    questions_attempted: int
    questions_correct: int
    questions_incorrect: int
    reasons: List[WeakReason]
    confidence: str
    last_attempt_at: Optional[datetime]


def _reason_confidence(reasons: List[WeakReason]) -> str:
    return "confirmed" if any(reason.code in _CONFIRMED_CLASSES for reason in reasons) else "low"


def classify_topic(
    *,
    attempts: int,
    questions_attempted: int,
    questions_incorrect: int,
    accuracy: float,
    recent_attempted: int = 0,
    recent_correct: int = 0,
) -> List[WeakReason]:
    """Applies every rule to one topic's counters and returns the ones that fired.

    Takes plain numbers rather than a ``TopicMastery`` row so the rules can be
    exercised without a database. ``recent_attempted``/``recent_correct`` are
    the last ``RECENT_WINDOW`` answers; when either is missing the
    recent-performance rule stays silent, so callers that only know the stored
    counters keep the exact behaviour they had before.
    """
    messages: dict = {}

    if questions_incorrect >= REPEATED_MISTAKE_COUNT:
        messages[REPEATED_MISTAKES] = (
            f"{questions_incorrect} incorrect answers across {attempts} "
            f"attempt{'s' if attempts != 1 else ''} on this topic."
        )

    if recent_attempted >= MIN_RECENT_EVIDENCE:
        recent_acc = recent_correct / recent_attempted
        if recent_acc * 100 <= RECENT_POOR_ACCURACY_THRESHOLD and recent_acc <= (
            accuracy / 100 - RECENT_DECLINE_MARGIN / 100
        ):
            messages[RECENT_POOR_PERFORMANCE] = (
                f"Recent performance declined: {recent_correct} of the last "
                f"{recent_attempted} answers were correct ({recent_acc * 100:.1f}%), "
                f"versus {accuracy}% overall."
            )

    if questions_attempted < MIN_ACCURACY_EVIDENCE:
        messages[INSUFFICIENT_EVIDENCE] = (
            f"Only {questions_attempted} question"
            f"{'s' if questions_attempted != 1 else ''} answered so far; "
            f"{MIN_ACCURACY_EVIDENCE} are needed before accuracy is meaningful."
        )
    elif accuracy < LOW_ACCURACY_THRESHOLD:
        messages[LOW_ACCURACY] = (
            f"Accuracy is {accuracy}% across {questions_attempted} questions, "
            f"below the {LOW_ACCURACY_THRESHOLD}% target."
        )

    return [
        WeakReason(code=code, message=messages[code])
        for code in REASON_ORDER
        if code in messages
    ]


def _to_weak_topic(
    mastery: TopicMastery, recent: Optional[Tuple[int, int]] = None
) -> Optional[WeakTopic]:
    recent_attempted, recent_correct = recent or (0, 0)
    reasons = classify_topic(
        attempts=mastery.attempts,
        questions_attempted=mastery.questions_attempted,
        questions_incorrect=mastery.questions_incorrect,
        accuracy=mastery.accuracy,
        recent_attempted=recent_attempted,
        recent_correct=recent_correct,
    )
    if not reasons:
        return None
    return WeakTopic(
        subject_id=mastery.subject_id,
        topic=mastery.topic,
        mastery_score=mastery.mastery_score,
        mastery_level=mastery.mastery_level,
        accuracy=mastery.accuracy,
        attempts=mastery.attempts,
        questions_attempted=mastery.questions_attempted,
        questions_correct=mastery.questions_correct,
        questions_incorrect=mastery.questions_incorrect,
        reasons=reasons,
        confidence=_reason_confidence(reasons),
        last_attempt_at=mastery.last_attempt_at,
    )


def detect_weak_topics(
    mastery_rows: Sequence[TopicMastery],
    recent_by_topic: Optional[Dict[str, Tuple[int, int]]] = None,
) -> List[WeakTopic]:
    """Returns the topics that trip at least one rule, ordered by topic name.

    A mastery row with no recorded answers is skipped: there is nothing to judge
    and it is not evidence of a weak topic. ``recent_by_topic`` maps topic name
    to ``(recent_attempted, recent_correct)`` and is optional -- without it the
    recent-performance rule has no data and contributes nothing.
    """
    recent_by_topic = recent_by_topic or {}
    weak: List[WeakTopic] = []
    for row in mastery_rows:
        # A row with no recorded answers carries no evidence either way.
        if row.questions_attempted <= 0:
            continue
        flagged = _to_weak_topic(row, recent_by_topic.get(row.topic))
        if flagged is not None:
            weak.append(flagged)
    weak.sort(key=lambda item: item.topic)
    return weak


async def load_weak_topics(
    db: AsyncSession, *, user_id: uuid.UUID, subject_id: uuid.UUID
) -> List[WeakTopic]:
    """Loads one user's topic mastery for a subject and flags the weak topics.

    Scoped to ``user_id`` on the mastery row itself, so a caller can never be
    handed another student's numbers even if subject ownership is checked
    somewhere else in the request. The recent-answer window is loaded from the
    answer log so the recent-performance rule has the data it needs.
    """
    items = list(
        (
            await db.execute(
                select(TopicMastery).where(
                    TopicMastery.user_id == user_id,
                    TopicMastery.subject_id == subject_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return detect_weak_topics(items, await _recent_by_topic(db, user_id, subject_id))


async def _recent_by_topic(
    db: AsyncSession, user_id: uuid.UUID, subject_id: uuid.UUID
) -> Dict[str, Tuple[int, int]]:
    """``(recent_attempted, recent_correct)`` per topic, from the answer log.

    Only the owning user's answers on the given subject are read, and only the
    last ``RECENT_WINDOW`` answers per topic are counted.
    """
    answers_result = await db.execute(
        select(QuizAttemptAnswer)
        .join(QuizAttempt, QuizAttempt.id == QuizAttemptAnswer.attempt_id)
        .where(
            QuizAttempt.user_id == user_id,
            QuizAttempt.subject_id == subject_id,
        )
        .order_by(QuizAttemptAnswer.created_at.asc())
    )
    recent_by_topic: Dict[str, List[QuizAttemptAnswer]] = {}
    for answer in answers_result.scalars().all():
        recent_by_topic.setdefault(answer.topic, []).append(answer)

    return {
        topic: (len(recent), sum(1 for answer in recent if answer.is_correct))
        for topic, answers in recent_by_topic.items()
        if (recent := answers[-RECENT_WINDOW:])
    }
