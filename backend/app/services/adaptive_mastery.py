"""Confidence-aware mastery estimates over the full answer history.

Phase 6 stores a topic's ``mastery_score`` from five counters. That number is
deliberately simple (``accuracy * confidence-gate``) so it can be updated
atomically on every submission. This module answers a different question: given
*every* recorded answer -- with its difficulty and timestamp -- plus the
flashcard evidence channel, what is a topic's best estimate of current mastery?

It is a pure, deterministic read-side computation. There are no columns, no
writes, and no randomness, and every weight is a documented constant. It is
used by the next-learning recommendation engine, which needs a mastery figure
that reflects the shape of the history rather than only its aggregate counters.

Formula
-------
1. **Weighted accuracy** (0-100). Every graded answer contributes evidence
   scaled by two independent factors:

   * difficulty ``w_d``: harder questions prove more ability per answer
     (``easy 0.7``, ``medium 1.0``, ``hard 1.4``, ``expert 1.8``; unknown
     values fall back to ``medium``);
   * recency ``w_r = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)``: recent
     answers matter more than stale ones, so improving (or slipping) over time
     shows up in the estimate. Repeating a topic simply adds more answers, and
     those answers carry a full recency weight.

   ``weighted_accuracy = 100 * sum(w_d * w_r for correct) / sum(w_d * w_r)``

2. **Flashcard channel** (0-100). Self-graded reviews are weaker evidence than
   graded answers, so when the channel has any reviews it is blended in at
   ``FLASHCARD_WEIGHT = 0.25`` and never dominates. With no graded answers at
   all, the flashcard rate alone is used rather than producing a meaningless
   zero. When *no flashcards have been reviewed*, the blend is skipped
   entirely: the absence of an optional channel is not weakness, and a perfect
   quiz history must not be dragged down by a channel the student never uses.

3. **Confidence gate**. ``confidence = min(1, evidence / EVIDENCE_TARGET)``
   where ``evidence = len(answers) + flashcard_reviews`` -- the same ramp the
   stored mastery score uses, so a thin history can never look mastered.

4. ``mastery = blended_accuracy * confidence``, rounded to one decimal, and the
   level comes from the same thresholds the rest of the app uses.

The result
:class:`MasteryEstimate` keeps the intermediate numbers alongside the final
``mastery`` so a caller can see exactly how the figure was derived and write it
into a recommendation reason.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.services.spaced_repetition import compute_flashcard_accuracy
from app.services.student_intelligence import EVIDENCE_TARGET, mastery_level

#: Evidence multiplier per question difficulty. Unknown difficulties fall back
#: to ``medium`` (1.0), which keeps the factor misbehaviour-free no matter what
#: a generator writes into the answers table.
DIFFICULTY_WEIGHTS = {
    "easy": 0.7,
    "medium": 1.0,
    "hard": 1.4,
    "expert": 1.8,
}

DEFAULT_DIFFICULTY = "medium"

#: Per-answer recency half-life in days. An answer loses half its weight every
#: three weeks, which is fast enough to surface a decline but slow enough that a
#: month-old strong stretch still counts.
RECENCY_HALF_LIFE_DAYS = 21.0

#: Ceiling the secondary, self-graded flashcard channel may contribute to a
#: topic's accuracy estimate while graded quiz answers exist.
FLASHCARD_WEIGHT = 0.25

#: How many of the most recent quiz answers feed ``recent_accuracy``.
RECENT_WINDOW = 5


@dataclass(frozen=True)
class ScoredAnswer:
    """One recorded graded answer, reduced to what mastery estimation needs."""

    difficulty: str
    is_correct: bool
    answered_at: datetime


@dataclass(frozen=True)
class MasteryEstimate:
    """A topic's mastery estimate plus the numbers that produced it."""

    mastery: float
    level: str
    confidence: float
    weighted_accuracy: float
    raw_accuracy: float
    recent_accuracy: Optional[float]
    difficulty_factor: float
    questions_attempted: int
    flashcard_reviews: int
    evidence_count: int
    last_activity_days: Optional[float]


def difficulty_index(difficulty: str) -> float:
    """The evidence weight for one answer's difficulty."""
    return DIFFICULTY_WEIGHTS.get(difficulty, DIFFICULTY_WEIGHTS[DEFAULT_DIFFICULTY])


def recency_weight(age_days: float) -> float:
    """Recency factor for an answer ``age_days`` old (1.0 fresh, decays to 0)."""
    return 0.5 ** (max(0.0, age_days) / RECENCY_HALF_LIFE_DAYS)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def difficulty_factor(answers: Sequence[ScoredAnswer]) -> float:
    """Mean difficulty index over the answers; 1.0 when there are none."""
    if not answers:
        return 1.0
    return sum(difficulty_index(answer.difficulty) for answer in answers) / len(answers)


def weighted_accuracy(answers: Sequence[ScoredAnswer], now: datetime) -> Optional[float]:
    """Difficulty- and recency-weighted quiz accuracy as a 0-100 percentage.

    Returns ``None`` when there are no answers so a caller can distinguish "no
    graded evidence" from "0% correct".
    """
    if not answers:
        return None
    weighted_correct = 0.0
    weighted_total = 0.0
    normalized_now = _as_naive_utc(now)
    for answer in answers:
        age_days = (normalized_now - _as_naive_utc(answer.answered_at)).total_seconds() / 86400.0
        weight = difficulty_index(answer.difficulty) * recency_weight(age_days)
        if answer.is_correct:
            weighted_correct += weight
        weighted_total += weight
    return round(weighted_correct / weighted_total * 100, 1)


def raw_accuracy(answers: Sequence[ScoredAnswer]) -> float:
    """Plain ``correct / answered`` percentage, mirroring the stored counter."""
    if not answers:
        return 0.0
    correct = sum(1 for answer in answers if answer.is_correct)
    return round(correct / len(answers) * 100, 1)


def recent_accuracy(answers: Sequence[ScoredAnswer]) -> Optional[float]:
    """Plain accuracy over the most recent ``RECENT_WINDOW`` answers."""
    ordered = sorted(answers, key=lambda answer: _as_naive_utc(answer.answered_at))
    recent = ordered[-RECENT_WINDOW:]
    if not recent:
        return None
    return raw_accuracy(recent)


def compute_adaptive_mastery(
    *,
    answers: Sequence[ScoredAnswer],
    flashcard_reviews: int = 0,
    flashcard_correct: int = 0,
    now: Optional[datetime] = None,
) -> Optional[MasteryEstimate]:
    """Estimates a topic's mastery from its full history, or ``None`` with no evidence.

    ``flashcard_reviews``/``flashcard_correct`` come from the mastery row's
    flashcard channel, which is maintained separately from the quiz channel.
    ``now`` defaults to UTC so the engine is deterministic given a database
    snapshot; tests pass an explicit ``now``.
    """
    resolved_now = _as_naive_utc(now or datetime.utcnow())
    evidence_count = len(answers) + flashcard_reviews
    if evidence_count <= 0:
        return None

    confidence = min(1.0, evidence_count / EVIDENCE_TARGET)
    quiz_rate = weighted_accuracy(answers, resolved_now) or 0.0
    flashcard_rate = compute_flashcard_accuracy(flashcard_correct, flashcard_reviews)

    if answers and flashcard_reviews > 0:
        blended_accuracy = (1.0 - FLASHCARD_WEIGHT) * quiz_rate + FLASHCARD_WEIGHT * flashcard_rate
    elif answers:
        blended_accuracy = quiz_rate
    else:
        blended_accuracy = flashcard_rate

    mastery = round(blended_accuracy * confidence, 1)

    last_activity_days = None
    if answers:
        latest = max(_as_naive_utc(answer.answered_at) for answer in answers)
        last_activity_days = round(max(0.0, (resolved_now - latest).total_seconds() / 86400.0), 1)

    return MasteryEstimate(
        mastery=mastery,
        level=mastery_level(mastery),
        confidence=round(confidence, 3),
        weighted_accuracy=quiz_rate,
        raw_accuracy=raw_accuracy(answers),
        recent_accuracy=recent_accuracy(answers),
        difficulty_factor=round(difficulty_factor(answers), 2),
        questions_attempted=len(answers),
        flashcard_reviews=flashcard_reviews,
        evidence_count=evidence_count,
        last_activity_days=last_activity_days,
    )