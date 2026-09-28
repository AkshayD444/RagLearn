"""Database access for spaced-repetition scheduling.

Kept apart from :mod:`app.services.spaced_repetition` (pure arithmetic) and from
the router (HTTP concerns) so the SQL can be read and tested on its own.

One decision is worth stating up front: every query here is scoped by
``user_id`` *and* joins through ``flashcard_decks``. A due card is only visible
if its deck is owned by the caller and has not been soft-deleted, so a deleted
deck's cards drop out of the queue without deleting the review history that the
progress panel still reports.
"""

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Integer, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql.elements import ColumnElement

from app.models.flashcard import Flashcard, FlashcardDeck, FlashcardReview
from app.services.spaced_repetition import (
    CardSchedule,
    next_schedule,
    reset_schedule,
    schedule_from_card,
)
from app.services.student_intelligence import normalize_topic


def due_condition(now: datetime) -> ColumnElement[bool]:
    """SQL predicate for "this card belongs in the review queue".

    A never-studied card (``due_at IS NULL``) is due immediately: generating a
    deck is a request to start learning it.
    """
    return or_(Flashcard.due_at.is_(None), Flashcard.due_at <= now)


def _scope(
    *,
    user_id: uuid.UUID,
    now: datetime,
    subject_id: Optional[uuid.UUID] = None,
    deck_id: Optional[uuid.UUID] = None,
) -> List[ColumnElement[bool]]:
    """The one definition of "a card this user is allowed to see and act on".

    Every read in this module goes through it. The ownership predicate and the
    due predicate are inseparable -- applying either without the other would
    leak either another user's cards or this user's future schedule -- so they
    are built together rather than left for each call site to assemble.
    """
    criteria: List[ColumnElement[bool]] = [
        FlashcardDeck.user_id == user_id,
        FlashcardDeck.deleted_at.is_(None),
        due_condition(now),
    ]
    if subject_id is not None:
        criteria.append(FlashcardDeck.subject_id == subject_id)
    if deck_id is not None:
        criteria.append(Flashcard.deck_id == deck_id)
    return criteria


def _joined():
    """SELECT root joined to the owning deck, the base for every query below."""
    return select(Flashcard).join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)


def _due_ordering():
    """Most-overdue first, then never-studied, then deck position.

    ``(due_at IS NULL)`` is used as the leading sort key rather than
    ``NULLS LAST`` because SQLite only learned that clause in 3.30 and the test
    suite still runs the whole flashcard path on SQLite. Both dialects evaluate
    ``IS NULL`` to 0/1, so the ordering is identical everywhere.
    """
    return (
        (Flashcard.due_at.is_(None)).asc(),
        Flashcard.due_at.asc(),
        Flashcard.position.asc(),
        Flashcard.id.asc(),
    )


async def load_due_cards(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
    subject_id: Optional[uuid.UUID] = None,
    deck_id: Optional[uuid.UUID] = None,
    limit: int = 20,
) -> List[Flashcard]:
    """The review queue, most overdue first.

    The deck is eager-loaded because every row in the response needs its
    ``subject_id`` and ``deck_id``; without it the router would issue one extra
    query per card.
    """
    result = await db.execute(
        _joined()
        .options(joinedload(Flashcard.deck))
        .where(*_scope(user_id=user_id, now=now, subject_id=subject_id, deck_id=deck_id))
        .order_by(*_due_ordering())
        .limit(limit)
    )
    return list(result.scalars().unique().all())


async def count_due_cards(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
    subject_id: Optional[uuid.UUID] = None,
    deck_id: Optional[uuid.UUID] = None,
) -> Tuple[int, int]:
    """Returns ``(due_count, new_count)`` for the same filters as :func:`load_due_cards`.

    Both numbers come from one statement so the count the dashboard renders and
    the cards it lists cannot disagree because a review landed between two round
    trips.
    """
    result = await db.execute(
        select(
            func.count(Flashcard.id),
            func.coalesce(
                func.sum(func.cast(Flashcard.due_at.is_(None), Integer)), 0
            ),
        )
        .select_from(Flashcard)
        .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
        .where(*_scope(user_id=user_id, now=now, subject_id=subject_id, deck_id=deck_id))
    )
    total, new_rows = result.one()
    return int(total), int(new_rows)


async def due_counts_by_topic(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    now: datetime,
) -> Dict[str, int]:
    """Due-card count per normalized topic, for the study planner.

    Topics are normalized in Python rather than SQL: the mapping is a short pure
    function whose exact behaviour is already covered by
    ``test_student_intelligence``, and a subject has few enough distinct topics
    that fetching them costs nothing.
    """
    result = await db.execute(
        select(Flashcard.topic, func.count(Flashcard.id))
        .select_from(Flashcard)
        .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
        .where(*_scope(user_id=user_id, now=now, subject_id=subject_id))
        .group_by(Flashcard.topic)
    )

    counts: Dict[str, int] = defaultdict(int)
    for topic, count in result.all():
        counts[normalize_topic(topic)] += int(count)
    return dict(counts)


async def due_counts_by_subject_topic(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_ids: Sequence[uuid.UUID],
    now: datetime,
) -> Dict[Tuple[uuid.UUID, str], int]:
    """Due-card count per ``(subject_id, topic)`` for the next-learning engine.

    The cross-subject next-learning recommendation needs one answer per subject
    rather than one aggregate, so this returns a two-keyed map instead of the
    single-subject :func:`due_counts_by_topic`. Topics are normalized the same
    way (``None``/blank becomes "General"), and only non-deleted decks owned by
    ``user_id`` with due cards are counted.
    """
    if not subject_ids:
        return {}
    result = await db.execute(
        select(FlashcardDeck.subject_id, Flashcard.topic, func.count(Flashcard.id))
        .select_from(Flashcard)
        .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
        .where(
            FlashcardDeck.user_id == user_id,
            FlashcardDeck.deleted_at.is_(None),
            FlashcardDeck.subject_id.in_(list(subject_ids)),
            due_condition(now),
        )
        .group_by(FlashcardDeck.subject_id, Flashcard.topic)
    )

    counts: Dict[Tuple[uuid.UUID, str], int] = defaultdict(int)
    for subject_id, topic, count in result.all():
        counts[(subject_id, normalize_topic(topic))] += int(count)
    return dict(counts)


async def deck_due_counts(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    deck_ids: Sequence[uuid.UUID],
    now: datetime,
) -> Dict[uuid.UUID, int]:
    """Due-card count per deck, for the deck browse list.

    Decks with nothing due are absent from the mapping rather than mapped to
    zero, so the caller can use a single ``.get(deck_id, 0)`` and skip a branch.
    """
    if not deck_ids:
        return {}
    result = await db.execute(
        select(Flashcard.deck_id, func.count(Flashcard.id))
        .select_from(Flashcard)
        .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
        .where(*_scope(user_id=user_id, now=now), Flashcard.deck_id.in_(list(deck_ids)))
        .group_by(Flashcard.deck_id)
    )
    return {deck_id: int(count) for deck_id, count in result.all()}


async def card_review_totals(
    db: AsyncSession, *, user_id: uuid.UUID, card_ids: Sequence[uuid.UUID]
) -> Dict[uuid.UUID, Tuple[int, int]]:
    """``(review_count, correct_count)`` per card, aggregated from the log.

    Scoped by ``user_id`` as well as ``card_id``: a card id alone would happily
    report another user's review history.
    """
    if not card_ids:
        return {}
    result = await db.execute(
        select(
            FlashcardReview.card_id,
            func.count(FlashcardReview.id),
            func.coalesce(
                func.sum(func.cast(FlashcardReview.is_correct, Integer)), 0
            ),
        )
        .where(
            FlashcardReview.user_id == user_id,
            FlashcardReview.card_id.in_(list(card_ids)),
        )
        .group_by(FlashcardReview.card_id)
    )
    return {card_id: (int(total), int(correct)) for card_id, total, correct in result.all()}


async def lock_cards_for_review(
    db: AsyncSession, *, deck_id: uuid.UUID
) -> List[Flashcard]:
    """Loads a deck's cards with row locks held until the transaction ends.

    The scheduler advances a card's state as a read-modify-write: the next
    interval depends on the current ease, repetitions, and interval. Two
    concurrent reviews of the same deck would otherwise both read the same state
    and one increment would be lost.

    ``FOR UPDATE`` is emitted on PostgreSQL and ignored on SQLite, so the
    concurrency guarantee is covered by the ``postgres``-marked integration
    tests rather than the SQLite default suite.
    """
    result = await db.execute(
        select(Flashcard)
        .where(Flashcard.deck_id == deck_id)
        .order_by(Flashcard.position, Flashcard.created_at, Flashcard.id)
        .with_for_update()
    )
    return list(result.scalars().all())


def apply_review_schedule(
    card: Flashcard, *, is_correct: bool, reviewed_at: datetime
) -> CardSchedule:
    """Advances one card's schedule in place and returns the new state.

    Pure arithmetic wrapped around a mutation so the caller never has to know
    which fields make up the schedule.
    """
    result = next_schedule(
        schedule_from_card(card),
        is_correct=is_correct,
        reviewed_at=reviewed_at,
    )
    card.ease_factor = result.schedule.ease_factor
    card.interval_days = result.schedule.interval_days
    card.repetitions = result.schedule.repetitions
    card.lapses = result.schedule.lapses
    card.due_at = result.due_at
    card.last_reviewed_at = reviewed_at
    return result.schedule


def reset_card_schedule(card: Flashcard) -> None:
    """Returns a card to its never-reviewed state, making it due again.

    Review history is left intact on purpose: forgetting a card is not the same
    as pretending the reviews never happened, and the progress panel counts them
    either way.
    """
    schedule = reset_schedule()
    card.ease_factor = schedule.ease_factor
    card.interval_days = schedule.interval_days
    card.repetitions = schedule.repetitions
    card.lapses = schedule.lapses
    # `due_at = None` is what puts the card back in the queue: the due predicate
    # treats a null due date as due immediately.
    card.due_at = None
    card.last_reviewed_at = None
