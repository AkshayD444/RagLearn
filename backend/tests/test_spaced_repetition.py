"""Unit tests for the pure spaced-repetition arithmetic.

These cover the three places an SM-2 style scheduler goes wrong in practice:
interval growth, the ease floor, and the lapse reset. No database, no clock --
``reviewed_at`` is always passed in, which is what makes the expected intervals
below exact rather than approximate.
"""

from datetime import datetime, timedelta

import pytest

from app.services.spaced_repetition import (
    DEFAULT_EASE_FACTOR,
    FLASHCARD_EVIDENCE_TARGET,
    MAX_EASE_FACTOR,
    MAX_INTERVAL_DAYS,
    MIN_EASE_FACTOR,
    CardSchedule,
    compute_flashcard_accuracy,
    compute_flashcard_mastery,
    is_new,
    next_schedule,
    reset_schedule,
    schedule_from_card,
)

NOW = datetime(2026, 3, 1, 12, 0, 0)


def test_new_card_is_default_state():
    """The persisted defaults must match the scheduler's notion of 'never studied'."""
    schedule = reset_schedule()
    assert schedule == CardSchedule()
    assert schedule.ease_factor == DEFAULT_EASE_FACTOR
    assert schedule.interval_days == 0
    assert schedule.repetitions == 0
    assert schedule.lapses == 0


def test_first_pass_schedules_one_day_out():
    result = next_schedule(CardSchedule(), is_correct=True, reviewed_at=NOW)
    assert result.schedule.interval_days == 1
    assert result.schedule.repetitions == 1
    assert result.due_at == NOW + timedelta(days=1)
    assert result.is_lapse is False


def test_second_pass_schedules_six_days_out():
    """The classic SM-2 1 / 6 / (interval x ease) progression."""
    after_first = next_schedule(CardSchedule(), is_correct=True, reviewed_at=NOW)
    after_second = next_schedule(
        after_first.schedule, is_correct=True, reviewed_at=NOW
    )
    assert after_second.schedule.interval_days == 6
    assert after_second.schedule.repetitions == 2


def test_third_pass_grows_the_interval_by_the_ease_factor():
    first = next_schedule(CardSchedule(), is_correct=True, reviewed_at=NOW)
    second = next_schedule(first.schedule, is_correct=True, reviewed_at=NOW)
    third = next_schedule(second.schedule, is_correct=True, reviewed_at=NOW)

    expected = round(6 * third.schedule.ease_factor)
    assert third.schedule.interval_days == expected
    assert third.schedule.interval_days > 6
    assert third.schedule.repetitions == 3


def test_fail_resets_repetitions_and_records_a_lapse():
    """A lapse means the material was forgotten, so the card restarts the ladder."""
    mature = CardSchedule(ease_factor=2.5, interval_days=120, repetitions=4)
    result = next_schedule(mature, is_correct=False, reviewed_at=NOW)

    assert result.is_lapse is True
    assert result.schedule.repetitions == 0
    assert result.schedule.interval_days == 1
    assert result.schedule.lapses == 1
    assert result.due_at == NOW + timedelta(days=1)
    # The expensive interval is gone; the ease is merely dented.
    assert result.schedule.ease_factor < 2.5


def test_repeated_lapses_accumulate_without_resetting_each_other():
    schedule = CardSchedule()
    for expected_lapses in (1, 2, 3):
        result = next_schedule(schedule, is_correct=False, reviewed_at=NOW)
        assert result.schedule.lapses == expected_lapses
        schedule = result.schedule


def test_ease_never_falls_below_the_floor():
    """A relearned card must stay recoverable, so the floor is 1.3 and not 1.0."""
    schedule = CardSchedule()
    for _ in range(50):
        result = next_schedule(schedule, is_correct=False, reviewed_at=NOW)
        assert result.schedule.ease_factor >= MIN_EASE_FACTOR
        schedule = result.schedule
    assert schedule.ease_factor == MIN_EASE_FACTOR


def test_ease_never_exceeds_the_ceiling():
    schedule = CardSchedule()
    for _ in range(50):
        result = next_schedule(schedule, is_correct=True, reviewed_at=NOW)
        assert result.schedule.ease_factor <= MAX_EASE_FACTOR
        schedule = result.schedule
    assert schedule.ease_factor == MAX_EASE_FACTOR


def test_interval_is_capped_at_one_year():
    """An unbounded interval would hide a next-review date the student can act on."""
    schedule = CardSchedule(ease_factor=2.8, interval_days=300, repetitions=9)
    result = next_schedule(schedule, is_correct=True, reviewed_at=NOW)
    assert result.schedule.interval_days == MAX_INTERVAL_DAYS


def test_recovery_after_a_lapse_still_grows():
    """A lapsed card must be able to work its way back up, not stay stuck at 1 day."""
    lapsed = next_schedule(
        CardSchedule(ease_factor=2.5, interval_days=200, repetitions=6),
        is_correct=False,
        reviewed_at=NOW,
    )
    first = next_schedule(lapsed.schedule, is_correct=True, reviewed_at=NOW)
    second = next_schedule(first.schedule, is_correct=True, reviewed_at=NOW)
    third = next_schedule(second.schedule, is_correct=True, reviewed_at=NOW)

    assert [first.schedule.interval_days, second.schedule.interval_days] == [1, 6]
    assert third.schedule.interval_days > 6
    assert third.schedule.repetitions == 3


def test_schedule_is_deterministic_for_identical_input():
    """The whole point of threading the clock in: a retry cannot drift."""
    a = next_schedule(CardSchedule(interval_days=6, repetitions=2), is_correct=True, reviewed_at=NOW)
    b = next_schedule(CardSchedule(interval_days=6, repetitions=2), is_correct=True, reviewed_at=NOW)
    assert a == b


def test_schedule_from_card_reads_orm_state():
    class FakeCard:
        ease_factor = 2.1
        interval_days = 12
        repetitions = 3
        lapses = 1

    assert schedule_from_card(FakeCard()) == CardSchedule(2.1, 12, 3, 1)


def test_schedule_from_card_tolerates_a_pre_migration_row():
    """Cards written before Phase 9 have no schedule columns loaded.

    ``getattr`` defaults keep an unmigrated-but-alive row schedulable instead of
    raising on a missing attribute.
    """

    class LegacyCard:
        pass

    assert schedule_from_card(LegacyCard()) == CardSchedule()


def test_is_new_distinguishes_never_studied_from_lapsed():
    assert is_new(None) is True
    assert is_new(NOW) is False


def test_flashcard_accuracy_is_percentage():
    assert compute_flashcard_accuracy(0, 0) == 0.0
    assert compute_flashcard_accuracy(3, 4) == 75.0
    assert compute_flashcard_accuracy(1, 3) == 33.3


def test_flashcard_mastery_is_gated_by_evidence():
    """Perfect accuracy on one review must not read as mastery."""
    assert compute_flashcard_mastery(0, 0) == 0.0
    assert compute_flashcard_mastery(1, 1) == 10.0
    assert compute_flashcard_mastery(5, 5) == 50.0


def test_flashcard_mastery_saturates_at_the_evidence_target():
    assert compute_flashcard_mastery(
        FLASHCARD_EVIDENCE_TARGET, FLASHCARD_EVIDENCE_TARGET
    ) == 100.0
    # Beyond the target the ramp is flat, so this is pure accuracy.
    assert compute_flashcard_mastery(8, 10) == 80.0


def test_flashcard_mastery_rewards_evidence_over_a_lucky_streak():
    """The comparison that justifies a separate evidence target for cards."""
    lucky = compute_flashcard_mastery(1, 1)
    proven = compute_flashcard_mastery(10, 10)
    assert lucky < proven


@pytest.mark.parametrize(
    "interval,repetitions,ease,expected",
    [
        # The ease is updated *first* and the new value drives the interval, which
        # is the classic SM-2 order. So these expected values are
        # round(interval * (ease + 0.1)) for a pass, not round(interval * ease).
        (0, 0, 2.5, 1),      # first pass ignores ease entirely
        (1, 1, 2.5, 6),      # second pass is the fixed 6-day rung
        (6, 2, 2.5, 16),     # round(6 * 2.6)
        (10, 2, 1.3, 14),    # round(10 * 1.4)
        (365, 9, 2.8, 365),  # capped, even though 365 * 2.9 would be ~1058
    ],
)
def test_interval_progression_table(interval, repetitions, ease, expected):
    schedule = CardSchedule(
        ease_factor=ease, interval_days=interval, repetitions=repetitions
    )
    assert (
        next_schedule(schedule, is_correct=True, reviewed_at=NOW).schedule.interval_days
        == expected
    )
