"""Integration tests for concurrent flashcard scheduling on real PostgreSQL.

SQLite ignores ``SELECT ... FOR UPDATE`` and serialises every write onto a single
connection, so the default suite structurally cannot observe a lost update. The
scheduler, though, is entirely a read-modify-write over each card's own state: the
next interval depends on the current ease, repetitions, and interval. Two
reviews of the same deck racing each other would both read the same starting
state, and one increment would vanish -- the card would silently not advance.

These tests pin that down on a real database. Run them with::

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
        python -m pytest -m postgres

Without ``TEST_DATABASE_URL`` (or when it is not a PostgreSQL URL) every test in
this module skips instantly, which keeps the default suite fast.
"""

import asyncio
import itertools
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select, text

from app.models.flashcard import Flashcard, FlashcardDeck
from app.services.flashcard_schedule import apply_review_schedule, lock_cards_for_review
from app.services.spaced_repetition import CardSchedule, next_schedule
from tests.conftest import seed_user_and_subject

pytestmark = pytest.mark.postgres

CONCURRENCY = 6
TOPIC = "Algebra"


class Barrier:
    """Releases every participant once ``total`` of them have arrived.

    Parked *before* the first locking read. Waiting afterwards would deadlock:
    the first transaction to reach ``SELECT ... FOR UPDATE`` would hold the row
    lock and then be waiting on peers that are blocked behind that same lock.
    """

    def __init__(self, total: int) -> None:
        self._total = total
        self._arrived = 0
        self._released = asyncio.Event()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            self._arrived += 1
            if self._arrived >= self._total:
                self._released.set()
        await self._released.wait()


@pytest.fixture
def barrier() -> Barrier:
    return Barrier(CONCURRENCY)


async def _seed_deck(session_factory, *, user_id: uuid.UUID, subject_id: uuid.UUID, cards: int = 1):
    """Creates a deck whose cards all start in the never-studied state."""
    async with session_factory() as session:
        deck = FlashcardDeck(
            user_id=user_id, subject_id=subject_id, title="Deck", card_count=cards
        )
        session.add(deck)
        await session.flush()
        for position in range(cards):
            session.add(
                Flashcard(
                    deck_id=deck.id,
                    front_text=f"front {position}",
                    back_text=f"back {position}",
                    topic=TOPIC,
                    position=position,
                )
            )
        await session.commit()
        await session.refresh(deck)
        return deck.id


async def _review_once(
    session_factory,
    barrier: Barrier,
    *,
    deck_id: uuid.UUID,
    is_correct: bool,
) -> CardSchedule:
    """One participant's full review transaction, parked before its locking read."""
    async with session_factory() as session:
        # Force the transaction (and its snapshot) open before the barrier, so the
        # peers genuinely overlap instead of running one after another.
        await session.execute(text("SELECT 1"))
        await barrier.wait()

        cards = await lock_cards_for_review(session, deck_id=deck_id)
        assert cards, "seeded deck should not be empty"
        for card in cards:
            apply_review_schedule(card, is_correct=is_correct, reviewed_at=datetime.utcnow())
        await session.commit()
        return cards[0].repetitions


async def _read_card(session_factory, *, deck_id: uuid.UUID) -> Flashcard:
    async with session_factory() as session:
        result = await session.execute(
            select(Flashcard).where(Flashcard.deck_id == deck_id)
        )
        return result.scalar_one()


async def test_concurrent_reviews_each_advance_the_schedule(pg_engine, barrier):
    """Every racing review must be applied, not just the one that commits last.

    Without the row lock this reads ``1`` instead of ``CONCURRENCY``: each
    transaction sees ``repetitions = 0``, writes ``1``, and the last writer wins.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Algebra")
    deck_id = await _seed_deck(pg_engine, user_id=uuid.UUID(user_id), subject_id=uuid.UUID(subject_id))

    await asyncio.gather(
        *[
            _review_once(pg_engine, barrier, deck_id=deck_id, is_correct=True)
            for _ in range(CONCURRENCY)
        ]
    )

    card = await _read_card(pg_engine, deck_id=deck_id)
    assert card.repetitions == CONCURRENCY


async def test_concurrent_reviews_accumulate_lapses(pg_engine, barrier):
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Algebra")
    deck_id = await _seed_deck(pg_engine, user_id=uuid.UUID(user_id), subject_id=uuid.UUID(subject_id))

    await asyncio.gather(
        *[
            _review_once(pg_engine, barrier, deck_id=deck_id, is_correct=False)
            for _ in range(CONCURRENCY)
        ]
    )

    card = await _read_card(pg_engine, deck_id=deck_id)
    assert card.lapses == CONCURRENCY
    # A lapse always resets the ladder, so this is the value regardless of races.
    assert card.repetitions == 0
    assert card.interval_days == 1


async def test_mixed_concurrent_passes_and_lapses_agree_with_the_scheduler(
    pg_engine, barrier
):
    """The persisted state must be *a* valid serial order of the reviews.

    Six racing reviews of one card, half passing and half lapsing. Whichever order
    PostgreSQL actually committed them in, the final state has to match what
    replaying that same sequence through the pure scheduler would produce --
    otherwise the stored state is not reachable and the audit trail is a lie.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Algebra")
    deck_id = await _seed_deck(pg_engine, user_id=uuid.UUID(user_id), subject_id=uuid.UUID(subject_id))

    outcomes = [True, False, True, False, True, False]
    await asyncio.gather(
        *[
            _review_once(pg_engine, barrier, deck_id=deck_id, is_correct=is_correct)
            for is_correct in outcomes
        ]
    )

    card = await _read_card(pg_engine, deck_id=deck_id)
    observed = (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.lapses,
    )

    # Replay every possible order and collect the reachable end states.
    reachable = set()
    for order in itertools.permutations(outcomes):
        schedule = CardSchedule()
        for is_correct in order:
            schedule = next_schedule(
                schedule, is_correct=is_correct, reviewed_at=datetime.utcnow()
            ).schedule
        reachable.add(
            (
                schedule.ease_factor,
                schedule.interval_days,
                schedule.repetitions,
                schedule.lapses,
            )
        )

    assert observed in reachable


async def test_locked_read_blocks_a_concurrent_writer(pg_engine):
    """The lock has to be held for the whole transaction, not just the SELECT.

    If the rows were re-read after commit, or the transaction closed right after
    the read, a second writer would interleave and the increments would be lost
    even though ``FOR UPDATE`` appeared in the query.

    No barrier here on purpose: the holder must reach its write on its own, so a
    shared barrier would simply deadlock waiting for peers that never arrive.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Algebra")
    deck_id = await _seed_deck(pg_engine, user_id=uuid.UUID(user_id), subject_id=uuid.UUID(subject_id))

    holding = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with pg_engine() as session:
            await session.execute(text("SELECT 1"))
            cards = await lock_cards_for_review(session, deck_id=deck_id)
            holding.set()
            # Hold the lock open long enough for the peer to prove it cannot write.
            await release.wait()
            for card in cards:
                card.repetitions += 1
            await session.commit()

    async def blocked_peer():
        await holding.wait()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_bump(pg_engine, deck_id=deck_id), timeout=0.1)
        release.set()

    await asyncio.gather(holder(), blocked_peer())

    card = await _read_card(pg_engine, deck_id=deck_id)
    assert card.repetitions == 1


async def _bump(session_factory, *, deck_id: uuid.UUID) -> None:
    """A write that must block while another transaction holds the row lock."""
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
        card = (
            await session.execute(select(Flashcard).where(Flashcard.deck_id == deck_id))
        ).scalar_one()
        card.repetitions += 1
        await session.commit()
