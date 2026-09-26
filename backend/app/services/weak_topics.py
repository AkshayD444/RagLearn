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

``insufficient_evidence``
    ``questions_attempted < MIN_ACCURACY_EVIDENCE``. Too few questions to judge
    accuracy at all. Flagged so the topic is not silently mistaken for a strong
    one; more practice is what resolves it.

A topic is only ever reported when it has at least one recorded answer, and a
topic that trips no rule is not a weak topic at all, so it is excluded.

Determinism
-----------
Topics are ordered alphabetically and each topic's reasons follow the fixed
``REASON_ORDER``. There is no priority score, so the output is a stable,
reproducible set rather than a ranked list.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mastery import TopicMastery

# Accuracy target: a topic below this percentage needs practice.
LOW_ACCURACY_THRESHOLD = 60.0

# Questions needed before accuracy is treated as meaningful.
MIN_ACCURACY_EVIDENCE = 3

# Incorrect answers on one topic that count as a repeated mistake.
REPEATED_MISTAKE_COUNT = 3

# Stable ordering for the reasons attached to a topic.
REASON_ORDER = ("repeated_mistakes", "low_accuracy", "insufficient_evidence")

REPEATED_MISTAKES = "repeated_mistakes"
LOW_ACCURACY = "low_accuracy"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"


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
    last_attempt_at: Optional[datetime]


def classify_topic(
    *,
    attempts: int,
    questions_attempted: int,
    questions_incorrect: int,
    accuracy: float,
) -> List[WeakReason]:
    """Applies every rule to one topic's counters and returns the ones that fired.

    Takes plain numbers rather than a ``TopicMastery`` row so the rules can be
    exercised without a database.
    """
    messages: dict = {}

    if questions_incorrect >= REPEATED_MISTAKE_COUNT:
        messages[REPEATED_MISTAKES] = (
            f"{questions_incorrect} incorrect answers across {attempts} "
            f"attempt{'s' if attempts != 1 else ''} on this topic."
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


def _to_weak_topic(mastery: TopicMastery) -> Optional[WeakTopic]:
    reasons = classify_topic(
        attempts=mastery.attempts,
        questions_attempted=mastery.questions_attempted,
        questions_incorrect=mastery.questions_incorrect,
        accuracy=mastery.accuracy,
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
        last_attempt_at=mastery.last_attempt_at,
    )


def detect_weak_topics(mastery_rows: Sequence[TopicMastery]) -> List[WeakTopic]:
    """Returns the topics that trip at least one rule, ordered by topic name.

    A mastery row with no recorded answers is skipped: there is nothing to judge
    and it is not evidence of a weak topic.
    """
    weak: List[WeakTopic] = []
    for row in mastery_rows:
        # A row with no recorded answers carries no evidence either way.
        if row.questions_attempted <= 0:
            continue
        flagged = _to_weak_topic(row)
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
    somewhere else in the request.
    """
    result = await db.execute(
        select(TopicMastery).where(
            TopicMastery.user_id == user_id,
            TopicMastery.subject_id == subject_id,
        )
    )
    return detect_weak_topics(list(result.scalars().all()))
