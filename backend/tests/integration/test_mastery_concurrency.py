"""Integration tests for concurrent topic-mastery accumulation on real PostgreSQL.

SQLite serialises every write onto a single connection, so the default suite
structurally cannot observe a lost update. Only a real database with row-level
locking exercises the race these tests cover: two quiz attempts submitted for
the same topic at the same time must both be counted.

Run them with the repository's Docker Compose database::

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
        python -m pytest -m postgres

Without ``TEST_DATABASE_URL`` (or when it is not a PostgreSQL URL) every test in
this module skips instantly, which keeps the default suite fast.
"""

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select, text

from app.models.mastery import TopicMastery
from app.services.student_intelligence import (
    EVIDENCE_TARGET,
    _apply_mastery_counters,
    _refresh_mastery,
    compute_accuracy,
    compute_mastery,
)
from tests.conftest import seed_user_and_subject

pytestmark = pytest.mark.postgres

CONCURRENCY = 6
TOPIC = "Algebra"


class Barrier:
    """Releases every participant once ``total`` of them have arrived.

    Placed *before* the first write of each transaction so that all
    transactions are provably open at the same time. Waiting afterwards would
    deadlock: the first writer to create a topic row holds the unique-index
    lock that the others block on, and it would be waiting on them.
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


async def _submit_answer(
    session_factory,
    barrier: Barrier,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    topic: str = TOPIC,
    correct: bool,
) -> None:
    """Commits one answer's worth of mastery counters, racing the peers.

    Each transaction is opened and parked on the barrier before it touches the
    topic row, so the increments below genuinely overlap.
    """
    async with session_factory() as session:
        # Forces PostgreSQL to open the transaction (and take its snapshot)
        # before the barrier, so the peers are guaranteed to overlap.
        await session.execute(text("SELECT 1"))
        await barrier.wait()

        mastery = await _apply_mastery_counters(
            session,
            user_id=user_id,
            subject_id=subject_id,
            topic=topic,
            attempted_delta=1,
            correct_delta=1 if correct else 0,
            incorrect_delta=0 if correct else 1,
            attempts_delta=1,
        )
        await _refresh_mastery(mastery, datetime.utcnow())
        await session.commit()


async def _read_mastery(session_factory, *, user_id, subject_id, topic=TOPIC):
    async with session_factory() as session:
        result = await session.execute(
            select(TopicMastery).where(
                TopicMastery.user_id == user_id,
                TopicMastery.subject_id == subject_id,
                TopicMastery.topic == topic,
            )
        )
        return result.scalar_one()


async def test_concurrent_first_writes_to_one_topic_share_a_single_row(pg_engine, barrier):
    """Simultaneous first attempts on a brand-new topic all land on one row.

    Every writer races to create the same ``(user, subject, topic)`` key. The
    savepoint-guarded insert means the losers absorb the IntegrityError and
    re-apply their deltas instead of failing the request.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Concurrent Create")

    await asyncio.gather(
        *[
            _submit_answer(
                pg_engine,
                barrier,
                user_id=uuid.UUID(user_id),
                subject_id=uuid.UUID(subject_id),
                correct=True,
            )
            for _ in range(CONCURRENCY)
        ]
    )

    row = await _read_mastery(
        pg_engine, user_id=uuid.UUID(user_id), subject_id=uuid.UUID(subject_id)
    )
    assert row.questions_attempted == CONCURRENCY
    assert row.questions_correct == CONCURRENCY
    assert row.questions_incorrect == 0


async def test_concurrent_attempts_do_not_lose_mastery_counter_increments(
    pg_engine, barrier
):
    """Mixed correct/incorrect answers on one topic keep every increment.

    This is the regression guard for the read-modify-write bug: counters are
    incremented as column expressions inside a single UPDATE, so the final
    totals must equal the number of submissions regardless of interleaving.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Concurrent Counters")
    user_uuid, subject_uuid = uuid.UUID(user_id), uuid.UUID(subject_id)

    # Seed the row first so this test measures lost updates, not the create race.
    async with pg_engine() as session:
        await _apply_mastery_counters(
            session,
            user_id=user_uuid,
            subject_id=subject_uuid,
            topic=TOPIC,
            attempted_delta=1,
            correct_delta=1,
            attempts_delta=1,
        )
        await session.commit()

    await asyncio.gather(
        *[
            _submit_answer(
                pg_engine,
                barrier,
                user_id=user_uuid,
                subject_id=subject_uuid,
                correct=index % 2 == 0,
            )
            for index in range(CONCURRENCY)
        ]
    )

    row = await _read_mastery(pg_engine, user_id=user_uuid, subject_id=subject_uuid)

    expected_incorrect = sum(1 for i in range(CONCURRENCY) if i % 2 != 0)
    expected_attempted = CONCURRENCY + 1

    assert row.questions_attempted == expected_attempted
    assert row.questions_correct == expected_attempted - expected_incorrect
    assert row.questions_incorrect == expected_incorrect

    # The invariant the mastery maths depends on.
    assert row.questions_correct + row.questions_incorrect == row.questions_attempted
    assert row.attempts == expected_attempted


async def test_mastery_derived_fields_stay_consistent_after_concurrent_attempts(
    pg_engine, barrier
):
    """Derived accuracy/mastery never drift away from the stored counters.

    ``_refresh_mastery`` is a pure function of the counters, so the row is
    self-consistent even though several writers refresh it concurrently.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Concurrent Derived")

    await asyncio.gather(
        *[
            _submit_answer(
                pg_engine,
                barrier,
                user_id=uuid.UUID(user_id),
                subject_id=uuid.UUID(subject_id),
                topic="Geometry",
                correct=False,
            )
            for _ in range(CONCURRENCY)
        ]
    )

    row = await _read_mastery(
        pg_engine,
        user_id=uuid.UUID(user_id),
        subject_id=uuid.UUID(subject_id),
        topic="Geometry",
    )

    assert row.questions_attempted == CONCURRENCY
    assert row.questions_correct == 0
    assert row.accuracy == 0.0
    assert row.mastery_score == 0.0
    assert row.mastery_level == "novice"
    assert row.accuracy == compute_accuracy(row.questions_correct, row.questions_attempted)
    assert row.mastery_score == compute_mastery(row.questions_correct, row.questions_attempted)


async def test_concurrent_attempts_accumulate_past_the_evidence_target(pg_engine):
    """More than ``EVIDENCE_TARGET`` perfect answers saturates at full mastery.

    ``compute_mastery`` caps confidence once the evidence target is reached, so
    surplus questions cannot push the score above 100.
    """
    user_id, subject_id = await seed_user_and_subject(pg_engine, title="Concurrent Mastered")
    user_uuid, subject_uuid = uuid.UUID(user_id), uuid.UUID(subject_id)

    total = EVIDENCE_TARGET + 3
    oversubscribed = Barrier(total)
    await asyncio.gather(
        *[
            _submit_answer(
                pg_engine,
                oversubscribed,
                user_id=user_uuid,
                subject_id=subject_uuid,
                topic="Mastered",
                correct=True,
            )
            for _ in range(total)
        ]
    )

    row = await _read_mastery(
        pg_engine, user_id=user_uuid, subject_id=subject_uuid, topic="Mastered"
    )
    assert row.questions_attempted == total
    assert row.accuracy == 100.0
    assert row.mastery_score == 100.0
    assert row.mastery_level == "mastered"
