import uuid
from datetime import datetime, timedelta
from typing import List, Tuple

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz, QuizQuestion
from app.models.subject import Subject
from app.rag.embeddings import embedding_service
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.adaptive_learning import (
    TopicMetrics,
    priority_score,
    recency_component,
    recommendation_type_and_reason,
)
from app.services.student_intelligence import (
    compute_accuracy,
    compute_mastery,
    mastery_level,
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


async def _create_subject(ac: AsyncClient, token: str, title: str = "Adaptive Subject"):
    res = await ac.post("/api/v1/subjects", json={"title": title}, headers=_auth(token))
    assert res.status_code == 201
    return res.json()["id"]


async def _seed_chunks(user_id: str, subject_id: str, title: str, texts: list):
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

        for i, text in enumerate(texts, start=1):
            embedding = await embedding_service.get_embedding(text)
            chunk = DocumentChunk(
                document_id=doc.id,
                subject_id=uuid.UUID(subject_id),
                user_id=uuid.UUID(user_id),
                chunk_index=i,
                page_number=i,
                token_count=max(1, len(text) // 4),
                content=text,
                embedding=embedding,
            )
            session.add(chunk)
        await session.commit()


async def _generate(ac: AsyncClient, token: str, subject_id: str, **overrides):
    payload = {"subject_id": subject_id, "question_count": 5, "difficulty": "medium"}
    payload.update(overrides)
    return await ac.post("/api/v1/quizzes/generate", json=payload, headers=_auth(token))


_db_cache = {}


async def _find_db_question(question_id: str):
    if question_id in _db_cache:
        return _db_cache[question_id]
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(QuizQuestion).where(QuizQuestion.id == uuid.UUID(question_id))
        )
        question = result.scalar_one()
    _db_cache[question_id] = question
    return question


async def _answers_for_quiz(quiz, select_correct: bool):
    answers = []
    for question in quiz["questions"]:
        db_q = await _find_db_question(question["id"])
        if select_correct:
            option = db_q.correct_option
        else:
            option = "A" if db_q.correct_option != "A" else "B"
        answers.append({"question_id": question["id"], "selected_option": option})
    return answers


@pytest.fixture(autouse=True)
def _clear_db_cache():
    _db_cache.clear()


async def _submit_all(ac: AsyncClient, token: str, quiz: dict, correct: bool):
    answers = await _answers_for_quiz(quiz, select_correct=correct)
    res = await ac.post(
        f"/api/v1/quizzes/{quiz['id']}/submit",
        json={"answers": answers},
        headers=_auth(token),
    )
    assert res.status_code == 200
    return res.json()


async def _seed_topic_history(
    user_id: str,
    subject_id: str,
    topic: str,
    outcomes: List[Tuple[bool, datetime]],
):
    """Directly seeds a Quiz, attempt, answers, and mastery row mirroring the
    quiz-submission persistence path. Each outcome is (is_correct, created_at)."""
    correct = sum(1 for ok, _ in outcomes if ok)
    attempted = len(outcomes)
    completed_at = outcomes[-1][1] if outcomes else datetime.utcnow()

    async with TestAsyncSessionLocal() as session:
        quiz = Quiz(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title=f"{topic} Quiz",
            difficulty="medium",
            question_count=attempted,
            topic=topic,
        )
        session.add(quiz)
        await session.flush()

        questions = []
        for i in range(attempted):
            question = QuizQuestion(
                quiz_id=quiz.id,
                question_text=f"Question {i + 1} for {topic}",
                option_a="Alpha",
                option_b="Beta",
                option_c="Gamma",
                option_d="Delta",
                correct_option="A",
                explanation="Seeded for adaptive tests.",
                difficulty="medium",
                topic=topic,
            )
            session.add(question)
            questions.append(question)
        await session.flush()

        attempt = QuizAttempt(
            user_id=uuid.UUID(user_id),
            quiz_id=quiz.id,
            subject_id=uuid.UUID(subject_id),
            score=correct,
            total_questions=attempted,
            percentage=round(correct / attempted * 100, 1) if attempted else 0.0,
            completed_at=completed_at,
        )
        session.add(attempt)
        await session.flush()

        for i, (is_correct, created_at) in enumerate(outcomes):
            session.add(
                QuizAttemptAnswer(
                    attempt_id=attempt.id,
                    question_id=questions[i].id,
                    question_text=questions[i].question_text,
                    selected_answer="A" if is_correct else "B",
                    correct_answer="A",
                    is_correct=is_correct,
                    difficulty="medium",
                    topic=topic,
                    created_at=created_at,
                )
            )

        mastery = TopicMastery(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            topic=topic,
            attempts=1,
            questions_attempted=attempted,
            questions_correct=correct,
            questions_incorrect=attempted - correct,
            accuracy=compute_accuracy(correct, attempted),
            mastery_score=compute_mastery(correct, attempted),
            mastery_level=mastery_level(compute_mastery(correct, attempted)),
            last_attempt_at=completed_at,
        )
        session.add(mastery)
        await session.commit()


async def _top(ac: AsyncClient, token: str, limit: int = None):
    url = "/api/v1/recommendations/top"
    if limit is not None:
        url = f"{url}?limit={limit}"
    return await ac.get(url, headers=_auth(token))


async def _subject_recs(ac: AsyncClient, token: str, subject_id: str, limit: int = None):
    url = f"/api/v1/recommendations/subjects/{subject_id}"
    if limit is not None:
        url = f"{url}?limit={limit}"
    return await ac.get(url, headers=_auth(token))


async def _seed_mastery_only(
    user_id: str,
    subject_id: str,
    topic: str,
    *,
    attempted: int,
    correct: int,
    last_attempt_at: datetime,
) -> None:
    """Seeds a TopicMastery row carrying counters but no answer rows.

    Leaves the recent-answer window empty while keeping the topic visible to
    the engine, which is the case the recency term has to score correctly.
    """
    mastery_score = compute_mastery(correct, attempted)
    async with TestAsyncSessionLocal() as session:
        session.add(
            TopicMastery(
                user_id=uuid.UUID(user_id),
                subject_id=uuid.UUID(subject_id),
                topic=topic,
                attempts=1,
                questions_attempted=attempted,
                questions_correct=correct,
                questions_incorrect=attempted - correct,
                accuracy=compute_accuracy(correct, attempted),
                mastery_score=mastery_score,
                mastery_level=mastery_level(mastery_score),
                last_attempt_at=last_attempt_at,
            )
        )
        await session.commit()


def _metrics(
    topic: str,
    *,
    attempts: int,
    attempted: int,
    correct: int,
    last_attempt_at: datetime,
    recent_attempted: int = 0,
    recent_correct: int = 0,
) -> TopicMetrics:
    """Builds self-consistent metrics, deriving accuracy and mastery from counters
    exactly as the quiz-submission path stores them."""
    mastery_score = compute_mastery(correct, attempted)
    return TopicMetrics(
        subject_id=uuid.uuid4(),
        subject_title="Unit Subject",
        topic=topic,
        attempts=attempts,
        questions_attempted=attempted,
        questions_correct=correct,
        questions_incorrect=attempted - correct,
        accuracy=compute_accuracy(correct, attempted),
        mastery_score=mastery_score,
        mastery_level=mastery_level(mastery_score),
        last_attempt_at=last_attempt_at,
        recent_attempted=recent_attempted,
        recent_correct=recent_correct,
    )


PASS_TEXTS = [
    "Photosynthesis converts sunlight into chemical energy stored in glucose molecules within plant leaves.",
    "Chloroplasts contain chlorophyll pigment that absorbs sunlight and drives a reaction stage inside the cell.",
]


@pytest.mark.asyncio
async def test_recommendations_require_authentication():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        top = await ac.get("/api/v1/recommendations/top")
        assert top.status_code == 401

        subject = await ac.get(
            f"/api/v1/recommendations/subjects/{uuid.uuid4()}"
        )
        assert subject.status_code == 401


@pytest.mark.asyncio
async def test_top_recommendations_only_contain_current_users_data():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "adaptive_owner@example.com")
        token_b, user_b = await _register(ac, "adaptive_intruder@example.com")

        subject_a = await _create_subject(ac, token_a, "Owner Subject")
        await _seed_chunks(user_a, subject_a, "Owner Notes.pdf", PASS_TEXTS)
        quiz = (await _generate(ac, token_a, subject_a, topic="OwnerTopic")).json()
        await _submit_all(ac, token_a, quiz, correct=False)

        subject_b = await _create_subject(ac, token_b, "Intruder Subject")
        await _seed_chunks(user_b, subject_b, "Intruder Notes.pdf", PASS_TEXTS)
        quiz_b = (await _generate(ac, token_b, subject_b, topic="IntruderTopic")).json()
        await _submit_all(ac, token_b, quiz_b, correct=False)

        recs_a = (await _top(ac, token_a, limit=10)).json()
        topics_a = {r["topic"] for r in recs_a}
        assert "OwnerTopic" in topics_a
        assert "IntruderTopic" not in topics_a
        assert all(r["subject_id"] == subject_a for r in recs_a)

        recs_b = (await _top(ac, token_b, limit=10)).json()
        topics_b = {r["topic"] for r in recs_b}
        assert "IntruderTopic" in topics_b
        assert "OwnerTopic" not in topics_b

        intruder_subject = await _subject_recs(ac, token_b, subject_a)
        assert intruder_subject.status_code == 404


@pytest.mark.asyncio
async def test_deleted_subjects_are_excluded():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_delete@example.com")
        subject_dead = await _create_subject(ac, token, "Doomed Subject")
        subject_alive = await _create_subject(ac, token, "Alive Subject")

        await _seed_chunks(user_id, subject_dead, "Doomed.pdf", PASS_TEXTS)
        await _seed_chunks(user_id, subject_alive, "Alive.pdf", PASS_TEXTS)

        quiz_doomed = (await _generate(ac, token, subject_dead, topic="DoomedTopic")).json()
        await _submit_all(ac, token, quiz_doomed, correct=False)
        quiz_alive = (await _generate(ac, token, subject_alive, topic="AliveTopic")).json()
        await _submit_all(ac, token, quiz_alive, correct=False)

        res = await ac.delete(f"/api/v1/subjects/{subject_dead}", headers=_auth(token))
        assert res.status_code == 204

        recs = (await _top(ac, token, limit=10)).json()
        topics = {r["topic"] for r in recs}
        assert "AliveTopic" in topics
        assert "DoomedTopic" not in topics

        subject_endpoint = await _subject_recs(ac, token, subject_dead)
        assert subject_endpoint.status_code == 404


@pytest.mark.asyncio
async def test_weak_topics_receive_appropriate_priority():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_weak@example.com")
        subject_id = await _create_subject(ac, token, "Weak vs Strong")
        await _seed_chunks(user_id, subject_id, "Notes.pdf", PASS_TEXTS)

        weak_quiz = (await _generate(ac, token, subject_id, topic="WeakTopic")).json()
        await _submit_all(ac, token, weak_quiz, correct=False)
        strong_quiz = (await _generate(ac, token, subject_id, topic="StrongTopic")).json()
        await _submit_all(ac, token, strong_quiz, correct=True)

        recs = (await _top(ac, token, limit=10)).json()
        by_topic = {r["topic"]: r for r in recs}

        weak = by_topic["WeakTopic"]
        strong = by_topic["StrongTopic"]
        assert weak["priority"] > strong["priority"]
        assert weak["accuracy"] == 0.0
        assert strong["accuracy"] == 100.0
        assert weak["recommendation_type"] == "REVIEW"
        assert strong["recommendation_type"] == "MAINTAIN"
        assert weak["questions_attempted"] == weak_quiz["question_count"]


@pytest.mark.asyncio
async def test_repeated_mistakes_increase_priority():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_repeat@example.com")
        subject_id = await _create_subject(ac, token, "Repeat Subject")
        await _seed_chunks(user_id, subject_id, "Repeat.pdf", PASS_TEXTS)

        quiz = (await _generate(ac, token, subject_id, topic="Calculus")).json()
        await _submit_all(ac, token, quiz, correct=False)

        recs = (await _top(ac, token)).json()
        first = next(r for r in recs if r["topic"] == "Calculus")

        await _submit_all(ac, token, quiz, correct=False)

        recs = (await _top(ac, token)).json()
        second = next(r for r in recs if r["topic"] == "Calculus")

        assert second["questions_attempted"] > first["questions_attempted"]
        assert second["questions_incorrect"] > first["questions_incorrect"]
        assert second["priority"] > first["priority"]


@pytest.mark.asyncio
async def test_stronger_mastery_reduces_priority():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_mastery@example.com")
        subject_id = await _create_subject(ac, token, "Mastery Subject")

        now = datetime.utcnow()
        await _seed_topic_history(
            user_id,
            subject_id,
            "LowMastery",
            [(True, now - timedelta(hours=1))] * 6 + [(False, now - timedelta(minutes=30))] * 4,
        )
        await _seed_topic_history(
            user_id,
            subject_id,
            "HighMastery",
            [(True, now - timedelta(hours=1))] * 10,
        )

        recs = (await _top(ac, token)).json()
        by_topic = {r["topic"]: r for r in recs}
        assert (
            by_topic["HighMastery"]["mastery"] > by_topic["LowMastery"]["mastery"]
        )
        assert (
            by_topic["HighMastery"]["priority"] < by_topic["LowMastery"]["priority"]
        )


@pytest.mark.asyncio
async def test_insufficient_evidence_does_not_dominate():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_evidence@example.com")
        subject_id = await _create_subject(ac, token, "Evidence Subject")

        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, subject_id, "OneCorrect", [(True, now)]
        )
        await _seed_topic_history(
            user_id, subject_id, "SolidWeak", [(False, now - timedelta(hours=1))] * 4 + [(True, now)]
        )

        recs = (await _top(ac, token, limit=10)).json()
        by_topic = {r["topic"]: r for r in recs}

        one_correct = by_topic["OneCorrect"]
        solid_weak = by_topic["SolidWeak"]
        assert solid_weak["priority"] > one_correct["priority"]
        assert one_correct["questions_attempted"] == 1
        assert recs.index(by_topic["SolidWeak"]) < recs.index(by_topic["OneCorrect"])


@pytest.mark.asyncio
async def test_recommendation_reasons_match_stored_data():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_reasons@example.com")
        subject_id = await _create_subject(ac, token, "Reasons Subject")

        now = datetime.utcnow()
        old = now - timedelta(days=10)

        # 5 answers, all wrong -> repeated mistakes
        await _seed_topic_history(
            user_id, subject_id, "Repeated", [(False, now - timedelta(hours=i)) for i in range(5)]
        )
        # 4 answers, 2 wrong -> low accuracy without repetition
        await _seed_topic_history(
            user_id, subject_id, "LowAcc",
            [(False, now - timedelta(hours=9)), (False, now - timedelta(hours=8)),
             (True, now - timedelta(hours=7)), (True, now - timedelta(hours=6))],
        )
        # 5 answers all correct -> maintain
        await _seed_topic_history(
            user_id, subject_id, "Solid",
            [(True, now - timedelta(hours=i)) for i in range(5)],
        )
        # recent weak performance after strong past performance -> decline
        await _seed_topic_history(
            user_id, subject_id, "Declined",
            [(True, old)] * 4 + [(False, now - timedelta(hours=2))] * 3,
        )

        recs = (await _top(ac, token, limit=10)).json()
        by_topic = {r["topic"]: r for r in recs}

        assert by_topic["Repeated"]["recommendation_type"] == "REVIEW"
        assert by_topic["Repeated"]["reason"] == "Repeated incorrect answers on this topic."

        attempted_low = by_topic["LowAcc"]["questions_attempted"]
        assert by_topic["LowAcc"]["recommendation_type"] == "REVIEW"
        assert (
            by_topic["LowAcc"]["reason"]
            == f"Low accuracy ({by_topic['LowAcc']['accuracy']}%) across {attempted_low} questions."
        )

        assert by_topic["Solid"]["recommendation_type"] == "MAINTAIN"
        assert (
            by_topic["Solid"]["reason"]
            == f"Strong accuracy (100.0%) across 5 questions."
        )

        assert by_topic["Declined"]["recommendation_type"] == "REVIEW"
        assert by_topic["Declined"]["reason"] == "Recent performance has declined."


@pytest.mark.asyncio
async def test_recommendation_ordering_is_deterministic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_deterministic@example.com")
        subject_id = await _create_subject(ac, token, "Deterministic Subject")

        now = datetime.utcnow()
        await _seed_topic_history(user_id, subject_id, "TopicA", [(False, now)] * 5)
        await _seed_topic_history(user_id, subject_id, "TopicB", [(False, now)] * 4)
        await _seed_topic_history(user_id, subject_id, "TopicC", [(True, now)] * 5)

        first = (await _top(ac, token, limit=10)).json()
        second = (await _top(ac, token, limit=10)).json()
        assert first == second
        assert [r["topic"] for r in first] == ["TopicA", "TopicB", "TopicC"]


@pytest.mark.asyncio
async def test_recommendation_limit_works():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_limit@example.com")
        subject_id = await _create_subject(ac, token, "Limit Subject")

        now = datetime.utcnow()
        for i in range(5):
            await _seed_topic_history(
                user_id, subject_id, f"BulkTopic{i}", [(False, now)] * 4
            )

        recs = (await _top(ac, token, limit=2)).json()
        assert len(recs) == 2
        assert [r["topic"] for r in recs] == ["BulkTopic0", "BulkTopic1"]

        subj_recs = (await _subject_recs(ac, token, subject_id, limit=3)).json()
        assert len(subj_recs) == 3

        invalid = await _top(ac, token, limit=0)
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_no_recommendation_data_leaks_between_users():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "adaptive_leak_a@example.com")
        token_b, user_b = await _register(ac, "adaptive_leak_b@example.com")

        subject_a = await _create_subject(ac, token_a, "A Subject")
        await _seed_chunks(user_a, subject_a, "A.pdf", PASS_TEXTS)
        quiz_a = (await _generate(ac, token_a, subject_a, topic="LeakyTopic")).json()
        await _submit_all(ac, token_a, quiz_a, correct=False)

        subject_b = await _create_subject(ac, token_b, "B Subject")
        await _seed_chunks(user_b, subject_b, "B.pdf", PASS_TEXTS)
        quiz_b = (await _generate(ac, token_b, subject_b, topic="BTopic")).json()
        await _submit_all(ac, token_b, quiz_b, correct=False)

        recs_b = (await _top(ac, token_b, limit=10)).json()
        assert all(r["subject_id"] == subject_b for r in recs_b)
        assert all("LeakyTopic" != r["topic"] for r in recs_b)

        subject_recs_b = await _subject_recs(ac, token_b, subject_a)
        assert subject_recs_b.status_code == 404


@pytest.mark.asyncio
async def test_empty_recommendations_for_new_user():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_empty@example.com")
        subject_id = await _create_subject(ac, token, "Fresh Subject")
        await _seed_chunks(user_id, subject_id, "Fresh.pdf", PASS_TEXTS)

        top = (await _top(ac, token)).json()
        assert top == []

        subject = (await _subject_recs(ac, token, subject_id)).json()
        assert subject == []


# ---------------------------------------------------------------------------
# Regression: a confidence-gated mastery score must not decide the action
# ---------------------------------------------------------------------------


def test_short_but_flawless_history_is_not_recommended_for_review():
    """A flawless history is MAINTAIN, even though the confidence-gated mastery
    score sits below WEAK_MASTERY purely because only two questions exist."""
    metrics = _metrics(
        "Flawless", attempts=2, attempted=2, correct=2, last_attempt_at=datetime.utcnow()
    )

    # The precondition that used to force REVIEW: mastery below the weak
    # boundary while accuracy is perfect.
    assert metrics.mastery_score == 20.0
    assert metrics.accuracy == 100.0

    recommendation_type, _ = recommendation_type_and_reason(metrics)
    assert recommendation_type == "MAINTAIN"


def test_flawless_history_is_never_reviewed_at_any_evidence_level():
    now = datetime.utcnow()
    for attempted in (1, 2, 3, 5, 8, 10, 20):
        metrics = _metrics(
            f"Flawless{attempted}",
            attempts=2,
            attempted=attempted,
            correct=attempted,
            last_attempt_at=now,
        )
        recommendation_type, _ = recommendation_type_and_reason(metrics)
        assert recommendation_type != "REVIEW", (
            f"{attempted} flawless answers were recommended for review "
            f"(mastery {metrics.mastery_score})"
        )


def test_low_accuracy_across_attempts_is_still_recommended_for_review():
    """The rule the fix had to preserve: genuinely weak topics are still REVIEW."""
    metrics = _metrics(
        "AllWrong",
        attempts=2,
        attempted=2,
        correct=0,
        last_attempt_at=datetime.utcnow(),
    )

    recommendation_type, reason = recommendation_type_and_reason(metrics)
    assert recommendation_type == "REVIEW"
    assert "Low accuracy" in reason


def test_short_history_above_the_weak_boundary_is_practice_not_review():
    """Half-correct over two questions is insufficient evidence, not a review
    demand — the same conclusion Phase 6 reaches via ``insufficient_evidence``."""
    metrics = _metrics(
        "HalfCorrect",
        attempts=2,
        attempted=2,
        correct=1,
        last_attempt_at=datetime.utcnow(),
    )

    recommendation_type, reason = recommendation_type_and_reason(metrics)
    assert recommendation_type == "PRACTICE"
    assert "Not enough questions yet" in reason


@pytest.mark.asyncio
async def test_api_keeps_a_flawless_short_history_out_of_review():
    """End-to-end guard for the reported case: two attempts and three flawless
    answers must not be served as a REVIEW recommendation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_flawless@example.com")
        subject_id = await _create_subject(ac, token, "Flawless Subject")
        await _seed_chunks(user_id, subject_id, "Flawless.pdf", PASS_TEXTS)

        first = (await _generate(ac, token, subject_id, question_count=2, topic="Flawless")).json()
        await _submit_all(ac, token, first, correct=True)
        second = (await _generate(ac, token, subject_id, question_count=1, topic="Flawless")).json()
        await _submit_all(ac, token, second, correct=True)

        recs = (await _top(ac, token, limit=10)).json()
        flawless = next(r for r in recs if r["topic"] == "Flawless")

        # The exact state that used to be misreported as REVIEW.
        assert flawless["attempts"] == 2
        assert flawless["questions_attempted"] == 3
        assert flawless["accuracy"] == 100.0
        assert flawless["mastery"] < 40.0
        assert flawless["recommendation_type"] != "REVIEW"
        assert flawless["recommendation_type"] == "MAINTAIN"


# ---------------------------------------------------------------------------
# Regression: missing recent evidence must not be scored as weak evidence
# ---------------------------------------------------------------------------


def test_unknown_recent_evidence_contributes_no_recency():
    assert recency_component(0.0, None) == 0.0
    assert recency_component(None, 0.0) == 0.0
    assert recency_component(None, None) == 0.0
    # A measured input still behaves exactly as documented.
    assert recency_component(0.0, 0.0) == 1.0
    assert recency_component(0.0, 1.0) == 0.0


def test_missing_recent_answers_do_not_raise_priority():
    """A topic with no recent answers must score like one whose recent answers
    were all correct: no evidence is not the worst evidence."""
    now = datetime.utcnow()
    without_recent = _metrics(
        "NoRecent", attempts=2, attempted=5, correct=5, last_attempt_at=now
    )
    with_perfect_recent = _metrics(
        "PerfectRecent",
        attempts=2,
        attempted=5,
        correct=5,
        last_attempt_at=now,
        recent_attempted=5,
        recent_correct=5,
    )

    assert priority_score(without_recent) == priority_score(with_perfect_recent)


def test_weak_recent_performance_still_raises_priority():
    now = datetime.utcnow()
    strong_recent = _metrics(
        "StrongRecent",
        attempts=2,
        attempted=5,
        correct=5,
        last_attempt_at=now,
        recent_attempted=5,
        recent_correct=5,
    )
    weak_recent = _metrics(
        "WeakRecent",
        attempts=2,
        attempted=5,
        correct=5,
        last_attempt_at=now,
        recent_attempted=5,
        recent_correct=0,
    )

    assert priority_score(weak_recent) > priority_score(strong_recent)


@pytest.mark.asyncio
async def test_api_scores_a_topic_without_recent_answers_like_a_strong_one():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "adaptive_recent@example.com")
        subject_id = await _create_subject(ac, token, "Recent Subject")

        now = datetime.utcnow()
        await _seed_mastery_only(
            user_id,
            subject_id,
            "NoRecent",
            attempted=5,
            correct=5,
            last_attempt_at=now,
        )
        await _seed_topic_history(
            user_id, subject_id, "PerfectRecent", [(True, now)] * 5
        )

        recs = (await _top(ac, token, limit=10)).json()
        by_topic = {r["topic"]: r for r in recs}

        assert by_topic["NoRecent"]["priority"] == by_topic["PerfectRecent"]["priority"]
        assert by_topic["NoRecent"]["questions_attempted"] == 5
        assert by_topic["NoRecent"]["accuracy"] == 100.0