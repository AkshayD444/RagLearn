"""SM-2 style spaced-repetition scheduling.

Pure and deterministic
----------------------
Every function here takes plain numbers and returns plain numbers. There is no
database access, no clock read, and no randomness: ``reviewed_at`` is always a
parameter. That is deliberate, for three reasons.

* The schedule is *recomputable*. Given a card's stored state and a grade, the
  next state is a total function, so a bug can be reproduced from a test case
  alone and a retried request cannot double-apply a lapse.
* It is testable at the boundary that actually matters. Interval growth, the
  ease floor, and the lapse reset are the three places this kind of scheduler
  goes wrong, and all three are one-line assertions here.
* The persistence layer stays a thin translation. :mod:`app.api.v1.flashcards`
  reads the card, calls :func:`next_schedule`, writes the result back.

Grading
-------
The review API is binary (``is_correct``), so grades are mapped onto the SM-2
quality scale at two points: a pass is the maximum quality, a fail is a
deliberately low-but-passing quality rather than zero. Zero is reserved for a
true blackout, which a self-graded correct/incorrect review cannot express.
Using a hard zero would drive the ease factor to its floor on a single miss and
make the scheduler unforgiving in a way the UI does not communicate.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# --- SM-2 constants ---------------------------------------------------------

DEFAULT_EASE_FACTOR = 2.5
#: Below this the interval collapses and a card can never grow again. SM-2
#: clamps here rather than at 1.0 to keep a relearned card recoverable.
MIN_EASE_FACTOR = 1.3
MAX_EASE_FACTOR = 2.8
#: A year is long enough that a next-review date is not yet actionable, and it
#: bounds the worst case for a single multiply.
MAX_INTERVAL_DAYS = 365
#: Days until a newly-passed or just-lapsed card comes back.
RELEARN_INTERVAL_DAYS = 1
#: The classic SM-2 1 / 6 / (interval x ease) progression.
FIRST_INTERVAL_DAYS = 1
SECOND_INTERVAL_DAYS = 6

#: SM-2 quality scale positions used for the binary self-grade.
QUALITY_PASS = 5
QUALITY_FAIL = 2

#: Reviews needed before a topic's flashcard evidence is treated as confident.
#: Independent of the quiz evidence target on purpose: a card review is weaker
#: evidence than a graded multiple-choice answer, so the two channels are
#: calibrated separately rather than sharing one constant.
FLASHCARD_EVIDENCE_TARGET = 10

MAX_EASE_DELTA = 0.1


@dataclass(frozen=True)
class CardSchedule:
    """The persisted scheduling state of one flashcard.

    ``due_at is None`` means "never studied", which is distinct from "studied
    and due now". A brand-new deck is immediately reviewable, so the two states
    are reported separately to the UI.
    """

    ease_factor: float = DEFAULT_EASE_FACTOR
    interval_days: int = 0
    repetitions: int = 0
    lapses: int = 0


@dataclass(frozen=True)
class ScheduleResult:
    """The next state for a card, plus what changed."""

    schedule: CardSchedule
    due_at: datetime
    is_lapse: bool


def schedule_from_card(card: object) -> CardSchedule:
    """Reads a schedule off an ORM ``Flashcard`` without importing the model.

    Taking ``object`` keeps this module free of a model import, which in turn
    keeps it importable from tests that only care about the arithmetic.
    """
    return CardSchedule(
        ease_factor=float(getattr(card, "ease_factor", DEFAULT_EASE_FACTOR)),
        interval_days=int(getattr(card, "interval_days", 0)),
        repetitions=int(getattr(card, "repetitions", 0)),
        lapses=int(getattr(card, "lapses", 0)),
    )


def is_new(due_at: Optional[datetime]) -> bool:
    """Whether a card has never been reviewed.

    A card that has lapsed is *not* new: it is familiar material that was
    forgotten, and the UI should say so rather than presenting it as unseen.
    """
    return due_at is None


def compute_flashcard_accuracy(correct: int, reviewed: int) -> float:
    """Percentage accuracy (0-100) for the flashcard evidence channel."""
    if reviewed <= 0:
        return 0.0
    return round(correct / reviewed * 100, 1)


def compute_flashcard_mastery(correct: int, reviewed: int) -> float:
    """Confidence-aware 0-100 mastery for the flashcard evidence channel.

    Mirrors the quiz formula in
    :func:`app.services.student_intelligence.compute_mastery` -- accuracy times
    an evidence ramp -- so a student reading both channels sees comparable
    numbers. The channels stay separate columns; only the shape is shared.
    """
    if reviewed <= 0:
        return 0.0
    accuracy = correct / reviewed
    confidence = min(1.0, reviewed / FLASHCARD_EVIDENCE_TARGET)
    return round(accuracy * confidence * 100, 1)


def _updated_ease(ease_factor: float, quality: int) -> float:
    """Applies the SM-2 ease update and clamps it into ``[1.3, 2.8]``."""
    delta = MAX_EASE_DELTA - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    updated = ease_factor + delta
    return round(min(MAX_EASE_FACTOR, max(MIN_EASE_FACTOR, updated)), 2)


def _next_interval(
    *, interval_days: int, repetitions: int, ease_factor: float
) -> int:
    """The SM-2 interval progression, capped at :data:`MAX_INTERVAL_DAYS`."""
    if repetitions <= 0:
        return FIRST_INTERVAL_DAYS
    if repetitions == 1:
        return SECOND_INTERVAL_DAYS
    grown = round(interval_days * ease_factor)
    return min(MAX_INTERVAL_DAYS, max(1, grown))


def next_schedule(
    current: CardSchedule, *, is_correct: bool, reviewed_at: datetime
) -> ScheduleResult:
    """Advances a card's schedule by one self-graded review.

    A pass walks the 1 / 6 / ``interval x ease`` progression and increments the
    repetition count. A fail records a lapse, resets the repetition count to
    zero, and returns the card to a one-day interval -- the next review is then
    identical to that of a new card, which is the intent: a lapse means the
    material was forgotten, not that it is nearly known.
    """
    quality = QUALITY_PASS if is_correct else QUALITY_FAIL
    ease_factor = _updated_ease(current.ease_factor, quality)

    if not is_correct:
        schedule = CardSchedule(
            ease_factor=ease_factor,
            interval_days=RELEARN_INTERVAL_DAYS,
            repetitions=0,
            lapses=current.lapses + 1,
        )
        is_lapse = True
    else:
        interval_days = _next_interval(
            interval_days=current.interval_days,
            repetitions=current.repetitions,
            ease_factor=ease_factor,
        )
        schedule = CardSchedule(
            ease_factor=ease_factor,
            interval_days=interval_days,
            repetitions=current.repetitions + 1,
            lapses=current.lapses,
        )
        is_lapse = False

    return ScheduleResult(
        schedule=schedule,
        due_at=reviewed_at + timedelta(days=schedule.interval_days),
        is_lapse=is_lapse,
    )


def reset_schedule() -> CardSchedule:
    """The state of a card that has never been studied.

    Used when a student says "treat this as new" -- for example after studying
    the material elsewhere. The single definition of that state, so the
    ``DEFAULT_EASE_FACTOR`` constant above is not duplicated as a literal in the
    persistence layer.
    """
    return CardSchedule()
