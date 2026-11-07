"""Tests for topic-level mastery retrieval and deterministic weak-topic detection.

The rule engine is exercised directly with plain counters (no database) so each
threshold is pinned independently, and the two new analytics endpoints are
covered end to end for ownership, ordering and edge cases.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.mastery import TopicMastery
from app.models.quiz import QuizQuestion
from app.rag.embeddings import embedding_service
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.student_intelligence import (
    compute_accuracy,
    compute_mastery,
    mastery_level,
)
from app.services.weak_topics import (
    INSUFFICIENT_EVIDENCE,
    LOW_ACCURACY,
    LOW_ACCURACY_THRESHOLD,
    MIN_ACCURACY_EVIDENCE,
    REPEATED_MISTAKES,
    REPEATED_MISTAKE_COUNT,
    REASON_ORDER,
    classify_topic,
    detect_weak_topics,
)
from tests.conftest import TestAsyncSessionLocal


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(ac: AsyncClient, email: str):
    res = await ac.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Test Student", "password": "password123"},
    )
    assert res.status_code == 201
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _create_subject(ac: AsyncClient, token: str, title: str = "Weak Topic Subject"):
    res = await ac.post("/api/v1/subjects", json={"title": title}, headers=_auth(token))
    assert res.status_code == 201
    return res.json()["id"]


def _mastery_row(
    topic: str,
    *,
    subject_id: uuid.UUID,
    user_id: uuid.UUID,
    attempts: int = 1,
    attempted: int = 0,
    correct: int = 0,
    accuracy: float | None = None,
    mastery_score: float | None = None,
    level: str | None = None,
    last_attempt_at: datetime | None = None,
) -> TopicMastery:
    """Builds an unsaved mastery row so the rules can be tested without a DB."""
    incorrect = attempted - correct
    resolved_accuracy = compute_accuracy(correct, attempted) if accuracy is None else accuracy
    resolved_mastery = compute_mastery(correct, attempted) if mastery_score is None else mastery_score
    return TopicMastery(
        user_id=user_id,
        subject_id=subject_id,
        topic=topic,
        attempts=attempts,
        questions_attempted=attempted,
        questions_correct=correct,
        questions_incorrect=incorrect,
        accuracy=resolved_accuracy,
        mastery_score=resolved_mastery,
        mastery_level=level or mastery_level(resolved_mastery),
        last_attempt_at=last_attempt_at,
        updated_at=datetime.utcnow(),
    )


async def _seed_mastery(user_id: str, subject_id: str, topic: str, **kwargs) -> None:
    """Persists one mastery row through the active test session."""
    async with TestAsyncSessionLocal() as session:
        session.add(
            _mastery_row(
                topic,
                subject_id=uuid.UUID(subject_id),
                user_id=uuid.UUID(user_id),
                **kwargs,
            )
        )
        await session.commit()


async def _seed_chunks(user_id: str, subject_id: str, title: str, texts: list) -> None:
    async with TestAsyncSessionLocal() as session:
        doc = Document(
            subject_id=uuid.UUID(subject_id),
            user_id=uuid.UUID(user_id),
            title=title,
            file_path="",
            file_size_bytes=1024,
            status="READY",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        for index, text in enumerate(texts, start=1):
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    subject_id=uuid.UUID(subject_id),
                    user_id=uuid.UUID(user_id),
                    chunk_index=index,
                    page_number=index,
                    token_count=max(1, len(text) // 4),
                    content=text,
                    embedding=await embedding_service.get_embedding(text),
                )
            )
        await session.commit()


async def _generate(ac: AsyncClient, token: str, subject_id: str, **overrides):
    payload = {"subject_id": subject_id, "question_count": 5, "difficulty": "medium"}
    payload.update(overrides)
    return await ac.post("/api/v1/quizzes/generate", json=payload, headers=_auth(token))


_db_cache: dict = {}


async def _find_db_question(question_id: str) -> QuizQuestion:
    if question_id not in _db_cache:
        async with TestAsyncSessionLocal() as session:
            result = await session.execute(
                select(QuizQuestion).where(QuizQuestion.id == uuid.UUID(question_id))
            )
            _db_cache[question_id] = result.scalar_one()
    return _db_cache[question_id]


async def _answers_for_quiz(quiz, select_correct: bool) -> list:
    answers = []
    for question in quiz["questions"]:
        db_question = await _find_db_question(question["id"])
        if select_correct:
            option = db_question.correct_option
        else:
            option = "A" if db_question.correct_option != "A" else "B"
        answers.append({"question_id": question["id"], "selected_option": option})
    return answers


@pytest.fixture(autouse=True)
def _clear_db_cache():
    _db_cache.clear()


async def _mastery(ac: AsyncClient, token: str, subject_id: str):
    return await ac.get(f"/api/v1/analytics/subjects/{subject_id}/mastery", headers=_auth(token))


async def _weak(ac: AsyncClient, token: str, subject_id: str, query: str = ""):
    suffix = f"?{query}" if query else ""
    return await ac.get(
        f"/api/v1/analytics/subjects/{subject_id}/weak-topics{suffix}",
        headers=_auth(token),
    )


# ---------------------------------------------------------------------------
# Rule engine: each threshold pinned independently of the database
# ---------------------------------------------------------------------------


def test_repeated_mistakes_fires_at_the_configured_count():
    below = classify_topic(attempts=1, questions_attempted=10, questions_incorrect=2, accuracy=80.0)
    assert REPEATED_MISTAKES not in [reason.code for reason in below]

    at_threshold = classify_topic(
        attempts=2, questions_attempted=10, questions_incorrect=REPEATED_MISTAKE_COUNT, accuracy=70.0
    )
    assert [reason.code for reason in at_threshold] == [REPEATED_MISTAKES]
    assert str(REPEATED_MISTAKE_COUNT) in at_threshold[0].message


def test_low_accuracy_requires_enough_evidence():
    # One wrong answer out of two: accuracy is low but evidence is too thin.
    thin = classify_topic(attempts=1, questions_attempted=2, questions_incorrect=1, accuracy=50.0)
    assert [reason.code for reason in thin] == [INSUFFICIENT_EVIDENCE]

    enough = classify_topic(
        attempts=1, questions_attempted=MIN_ACCURACY_EVIDENCE, questions_incorrect=2, accuracy=33.3
    )
    assert [reason.code for reason in enough] == [LOW_ACCURACY]


def test_accuracy_exactly_at_threshold_is_not_weak():
    at_target = classify_topic(
        attempts=1,
        questions_attempted=10,
        questions_incorrect=3,
        accuracy=LOW_ACCURACY_THRESHOLD,
    )
    # 3 incorrect answers still trips repeated_mistakes, but never low_accuracy.
    assert LOW_ACCURACY not in [reason.code for reason in at_target]


def test_strong_topic_triggers_no_rules():
    assert classify_topic(attempts=3, questions_attempted=10, questions_incorrect=0, accuracy=100.0) == []


def test_reasons_follow_the_fixed_documented_order():
    reasons = classify_topic(
        attempts=2, questions_attempted=4, questions_incorrect=3, accuracy=25.0
    )
    codes = [reason.code for reason in reasons]
    assert codes == [REPEATED_MISTAKES, LOW_ACCURACY]
    assert codes == [code for code in REASON_ORDER if code in codes]


def test_messages_cite_the_underlying_numbers():
    reasons = classify_topic(attempts=2, questions_attempted=10, questions_incorrect=6, accuracy=40.0)
    by_code = {reason.code: reason.message for reason in reasons}
    assert "6 incorrect answers" in by_code[REPEATED_MISTAKES]
    assert "2 attempts" in by_code[REPEATED_MISTAKES]
    assert "40.0%" in by_code[LOW_ACCURACY]
    assert str(LOW_ACCURACY_THRESHOLD) in by_code[LOW_ACCURACY]


def test_single_attempt_and_single_question_are_phrased_in_the_singular():
    repeated = classify_topic(attempts=1, questions_attempted=4, questions_incorrect=3, accuracy=25.0)
    assert "across 1 attempt on this topic" in repeated[0].message

    thin = classify_topic(attempts=1, questions_attempted=1, questions_incorrect=0, accuracy=100.0)
    assert "Only 1 question answered" in thin[0].message


def test_topics_with_no_recorded_answers_are_never_flagged():
    user_id, subject_id = uuid.uuid4(), uuid.uuid4()
    empty = _mastery_row("Never Attempted", subject_id=subject_id, user_id=user_id)
    assert empty.questions_attempted == 0
    assert detect_weak_topics([empty]) == []


def test_detect_weak_topics_excludes_strong_topics_and_orders_alphabetically():
    user_id, subject_id = uuid.uuid4(), uuid.uuid4()
    rows = [
        _mastery_row("Zoology", subject_id=subject_id, user_id=user_id, attempts=1, attempted=10, correct=10),
        _mastery_row("Algebra", subject_id=subject_id, user_id=user_id, attempts=1, attempted=10, correct=1),
        _mastery_row("Botany", subject_id=subject_id, user_id=user_id, attempts=1, attempted=4, correct=3),
        _mastery_row("Astronomy", subject_id=subject_id, user_id=user_id),
    ]

    detected = detect_weak_topics(rows)

    assert [item.topic for item in detected] == ["Algebra"]
    assert detected[0].questions_incorrect == 9
    assert {reason.code for reason in detected[0].reasons} == {REPEATED_MISTAKES, LOW_ACCURACY}


def test_detect_weak_topics_is_deterministic_across_input_order():
    user_id, subject_id = uuid.uuid4(), uuid.uuid4()
    rows = [
        _mastery_row(topic, subject_id=subject_id, user_id=user_id, attempts=1, attempted=5, correct=1)
        for topic in ("Waves", "Acids", "Magnetism", "Genetics")
    ]
    first = [item.topic for item in detect_weak_topics(rows)]
    second = [item.topic for item in detect_weak_topics(list(reversed(rows)))]
    assert first == second == ["Acids", "Genetics", "Magnetism", "Waves"]


# ---------------------------------------------------------------------------
# GET /analytics/subjects/{id}/mastery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topic_mastery_returns_every_topic_weakest_first():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "mastery_order@example.com")
        subject_id = await _create_subject(ac, token)

        await _seed_mastery(user_id, subject_id, "Strong", attempts=2, attempted=10, correct=10)
        await _seed_mastery(user_id, subject_id, "Weak", attempts=1, attempted=5, correct=1)
        await _seed_mastery(user_id, subject_id, "Middling", attempts=1, attempted=5, correct=3)

        res = await _mastery(ac, token, subject_id)
        assert res.status_code == 200
        data = res.json()

        assert [item["topic"] for item in data] == ["Weak", "Middling", "Strong"]
        assert data[0]["mastery_score"] < data[1]["mastery_score"] < data[2]["mastery_score"]
        assert data[2]["mastery_level"] == "mastered"
        assert all(item["subject_id"] == subject_id for item in data)


@pytest.mark.asyncio
async def test_topic_mastery_is_empty_without_attempts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, _ = await _register(ac, "mastery_empty@example.com")
        subject_id = await _create_subject(ac, token)

        res = await _mastery(ac, token, subject_id)
        assert res.status_code == 200
        assert res.json() == []


@pytest.mark.asyncio
async def test_topic_mastery_does_not_break_the_existing_performance_route():
    """The established /performance ordering is left untouched by the new route."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "mastery_performance@example.com")
        subject_id = await _create_subject(ac, token)

        await _seed_mastery(user_id, subject_id, "Alpha", attempts=1, attempted=5, correct=1)
        await _seed_mastery(user_id, subject_id, "Beta", attempts=2, attempted=10, correct=10)

        performance = await ac.get(
            f"/api/v1/analytics/subjects/{subject_id}/performance", headers=_auth(token)
        )
        assert performance.status_code == 200
        # /performance has always listed the strongest topic first.
        assert [item["topic"] for item in performance.json()["mastery"]] == ["Beta", "Alpha"]

        mastery = await _mastery(ac, token, subject_id)
        assert [item["topic"] for item in mastery.json()] == ["Alpha", "Beta"]


# ---------------------------------------------------------------------------
# GET /analytics/subjects/{id}/weak-topics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weak_topics_returns_flagged_topics_with_reasons():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_basic@example.com")
        subject_id = await _create_subject(ac, token)

        await _seed_mastery(user_id, subject_id, "Photosynthesis", attempts=2, attempted=10, correct=2)
        await _seed_mastery(user_id, subject_id, "Genetics", attempts=1, attempted=5, correct=5)

        res = await _weak(ac, token, subject_id)
        assert res.status_code == 200
        data = res.json()

        # A perfect topic is not a weak topic.
        assert [item["topic"] for item in data] == ["Photosynthesis"]
        entry = data[0]
        assert entry["subject_id"] == subject_id
        assert entry["questions_attempted"] == 10
        assert entry["questions_correct"] == 2
        assert entry["questions_incorrect"] == 8
        assert entry["accuracy"] == 20.0
        assert entry["reasons"][0]["code"] == REPEATED_MISTAKES
        assert {reason["code"] for reason in entry["reasons"]} == {REPEATED_MISTAKES, LOW_ACCURACY}
        assert all(reason["message"] for reason in entry["reasons"])


@pytest.mark.asyncio
async def test_weak_topics_flags_a_topic_with_too_little_evidence():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_thin@example.com")
        subject_id = await _create_subject(ac, token)

        # One question, answered correctly: not wrong, just unproven.
        await _seed_mastery(user_id, subject_id, "Cell Division", attempts=1, attempted=1, correct=1)

        res = await _weak(ac, token, subject_id)
        assert res.status_code == 200
        data = res.json()
        assert [item["topic"] for item in data] == ["Cell Division"]
        assert [reason["code"] for reason in data[0]["reasons"]] == [INSUFFICIENT_EVIDENCE]
        assert data[0]["accuracy"] == 100.0


@pytest.mark.asyncio
async def test_weak_topics_is_empty_when_nothing_is_weak():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_none@example.com")
        subject_id = await _create_subject(ac, token)

        res = await _weak(ac, token, subject_id)
        assert res.status_code == 200
        assert res.json() == []

        await _seed_mastery(user_id, subject_id, "Solid", attempts=2, attempted=10, correct=9)
        after = await _weak(ac, token, subject_id)
        assert after.status_code == 200
        assert after.json() == []


@pytest.mark.asyncio
async def test_weak_topics_honours_the_limit_and_rejects_invalid_values():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_limit@example.com")
        subject_id = await _create_subject(ac, token)

        for topic in ("Alpha", "Bravo", "Charlie"):
            await _seed_mastery(user_id, subject_id, topic, attempts=1, attempted=5, correct=1)

        limited = await _weak(ac, token, subject_id, "limit=2")
        assert limited.status_code == 200
        assert [item["topic"] for item in limited.json()] == ["Alpha", "Bravo"]

        for bad in ("limit=0", "limit=-1", "limit=1000"):
            res = await _weak(ac, token, subject_id, bad)
            assert res.status_code == 422, bad


@pytest.mark.asyncio
async def test_weak_topics_and_mastery_are_scoped_to_the_owning_user(forget_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        owner_token, owner_id = await _register(ac, "weak_owner@example.com")
        intruder_token, _ = await _register(ac, "weak_intruder@example.com")

        subject_id = await _create_subject(ac, owner_token, "Owner Subject")
        await _seed_mastery(owner_id, subject_id, "Algebra", attempts=1, attempted=5, correct=1)

        for call in (_mastery, _weak):
            res = await call(ac, intruder_token, subject_id)
            assert res.status_code == 404
            assert "access denied" in res.json()["detail"].lower()

            unknown = await call(ac, owner_token, str(uuid.uuid4()))
            assert unknown.status_code == 404

            forget_session(ac)
            unauthenticated = await ac.get(
                f"/api/v1/analytics/subjects/{subject_id}/weak-topics"
            )
            assert unauthenticated.status_code in (401, 403)


@pytest.mark.asyncio
async def test_weak_topics_does_not_leak_across_users_within_the_same_subject_scope():
    """A second student's mastery is never merged into the first student's list."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "weak_iso_a@example.com")
        token_b, user_b = await _register(ac, "weak_iso_b@example.com")

        subject_a = await _create_subject(ac, token_a, "A Subject")
        subject_b = await _create_subject(ac, token_b, "B Subject")

        await _seed_mastery(user_a, subject_a, "Shared Name", attempts=1, attempted=5, correct=1)
        await _seed_mastery(user_b, subject_b, "Shared Name", attempts=1, attempted=5, correct=5)

        data_a = (await _weak(ac, token_a, subject_a)).json()
        data_b = (await _weak(ac, token_b, subject_b)).json()

        assert [item["topic"] for item in data_a] == ["Shared Name"]
        assert data_a[0]["questions_incorrect"] == 4
        # Student B answered everything correctly, so B has no weak topics.
        assert data_b == []


@pytest.mark.asyncio
async def test_analytics_history_survives_a_soft_deleted_subject():
    """Consistent with /performance: a deleted subject stays readable."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_deleted@example.com")
        subject_id = await _create_subject(ac, token, "Doomed Subject")
        await _seed_mastery(user_id, subject_id, "Algebra", attempts=1, attempted=5, correct=1)

        deleted = await ac.delete(f"/api/v1/subjects/{subject_id}", headers=_auth(token))
        assert deleted.status_code == 204

        assert (await _weak(ac, token, subject_id)).status_code == 200
        assert (await _mastery(ac, token, subject_id)).status_code == 200


# ---------------------------------------------------------------------------
# End-to-end: real quiz submissions feed the detector
# ---------------------------------------------------------------------------

PASS_TEXTS = [
    "Photosynthesis converts sunlight into chemical energy stored in glucose molecules within plant leaves.",
    "Chloroplasts contain chlorophyll pigment that absorbs sunlight and drives a reaction stage inside the cell.",
]


@pytest.mark.asyncio
async def test_weak_topics_reflects_real_submitted_attempts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_e2e@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, "Weak Ch1.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id, topic="Photosynthesis")
        assert gen.status_code == 201, gen.text
        quiz = gen.json()

        wrong = await _answers_for_quiz(quiz, select_correct=False)
        res = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": wrong},
            headers=_auth(token),
        )
        assert res.status_code == 200
        assert res.json()["score"] == 0

        weak = (await _weak(ac, token, subject_id)).json()
        assert [item["topic"] for item in weak] == ["Photosynthesis"]
        assert weak[0]["questions_attempted"] == len(quiz["questions"])
        assert weak[0]["questions_incorrect"] == len(quiz["questions"])
        # Enough questions were answered for accuracy to be meaningful, so the
        # insufficient-evidence rule stays silent here.
        assert {reason["code"] for reason in weak[0]["reasons"]} == {
            REPEATED_MISTAKES,
            LOW_ACCURACY,
        }

        mastery = (await _mastery(ac, token, subject_id)).json()
        assert len(mastery) == 1
        assert mastery[0]["topic"] == "Photosynthesis"
        assert mastery[0]["mastery_score"] == 0.0
        assert mastery[0]["mastery_level"] == "novice"

        # Recovering on a second attempt is reflected too.
        correct = await _answers_for_quiz(quiz, select_correct=True)
        res = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": correct},
            headers=_auth(token),
        )
        assert res.status_code == 200

        mastery = (await _mastery(ac, token, subject_id)).json()
        assert mastery[0]["attempts"] == 2
        assert mastery[0]["questions_attempted"] == 2 * len(quiz["questions"])
        assert mastery[0]["questions_correct"] == len(quiz["questions"])
        assert mastery[0]["questions_incorrect"] == len(quiz["questions"])
        assert mastery[0]["last_attempt_at"] is not None


@pytest.mark.asyncio
async def test_untagged_answers_are_reported_under_the_general_topic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_general@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, "Weak Ch2.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id)
        quiz = gen.json()
        wrong = await _answers_for_quiz(quiz, select_correct=False)
        res = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": wrong},
            headers=_auth(token),
        )
        assert res.status_code == 200

        weak = (await _weak(ac, token, subject_id)).json()
        assert [item["topic"] for item in weak] == ["General"]


@pytest.mark.asyncio
async def test_stale_mastery_rows_are_ignored_when_no_attempt_is_recorded():
    """A zero-question row is not weak evidence, even if counters are odd."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "weak_stale@example.com")
        subject_id = await _create_subject(ac, token)

        now = datetime.utcnow() - timedelta(days=3)
        await _seed_mastery(
            user_id, subject_id, "Dormant", attempts=0, attempted=0, correct=0, last_attempt_at=now
        )

        res = await _weak(ac, token, subject_id)
        assert res.status_code == 200
        assert res.json() == []

        mastery = await _mastery(ac, token, subject_id)
        assert [item["topic"] for item in mastery.json()] == ["Dormant"]
        assert mastery.json()[0]["last_attempt_at"] is not None
