import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.mastery import TopicMastery
from app.models.quiz import QuizQuestion
from app.rag.embeddings import embedding_service
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
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


async def _create_subject(ac: AsyncClient, token: str, title: str = "Intelligence Subject"):
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

    return str(doc.id)


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


async def _submit_with_answers(
    ac: AsyncClient, token: str, quiz_id: str, answers: list, **extra
):
    payload = {"answers": answers}
    payload.update(extra)
    return await ac.post(
        f"/api/v1/quizzes/{quiz_id}/submit", json=payload, headers=_auth(token)
    )


PASS_TEXTS = [
    "Photosynthesis converts sunlight into chemical energy stored in glucose molecules within plant leaves.",
    "Chloroplasts contain chlorophyll pigment that absorbs sunlight and drives a reaction stage inside the cell.",
]


async def _performance(ac: AsyncClient, token: str, subject_id: str):
    return await ac.get(
        f"/api/v1/analytics/subjects/{subject_id}/performance", headers=_auth(token)
    )


async def _db_attempts(user_id: str) -> list:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(QuizAttempt).where(QuizAttempt.user_id == uuid.UUID(user_id))
        )
        return list(result.scalars().all())


async def _db_answers_for(attempt_id: uuid.UUID) -> list:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(QuizAttemptAnswer).where(QuizAttemptAnswer.attempt_id == attempt_id)
        )
        return list(result.scalars().all())


async def _db_mastery(user_id: str, subject_id: str, topic: str):
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(TopicMastery).where(
                TopicMastery.user_id == uuid.UUID(user_id),
                TopicMastery.subject_id == uuid.UUID(subject_id),
                TopicMastery.topic == topic,
            )
        )
        return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_quiz_submit_persists_attempt_and_answer_snapshots():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "intel_persist@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, "Intel Ch1.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id)
        quiz = gen.json()
        answers = await _answers_for_quiz(quiz, select_correct=True)

        res = await _submit_with_answers(ac, token, quiz["id"], answers)
        assert res.status_code == 200
        data = res.json()
        assert data["attempt_id"]
        assert data["correct_answers"] == len(answers)
        assert data["incorrect_answers"] == 0
        assert data["score"] == len(answers)
        assert data["percentage"] == 100.0

        attempts = await _db_attempts(user_id)
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.quiz_id == uuid.UUID(quiz["id"])
        assert attempt.subject_id == uuid.UUID(subject_id)
        assert attempt.user_id == uuid.UUID(user_id)
        assert attempt.score == len(answers)
        assert attempt.total_questions == len(answers)
        assert attempt.percentage == 100.0
        assert attempt.started_at is None
        assert attempt.completed_at is not None

        saved_answers = await _db_answers_for(attempt.id)
        assert len(saved_answers) == len(answers)
        assert all(a.is_correct for a in saved_answers)
        assert all(a.correct_answer == a.selected_answer for a in saved_answers)
        assert all(a.difficulty == "medium" for a in saved_answers)
        assert set(a.question_id for a in saved_answers) == set(
            uuid.UUID(q["id"]) for q in quiz["questions"]
        )


@pytest.mark.asyncio
async def test_attempt_stores_topic_and_timing_metadata():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "intel_topic@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, "Intel Ch2.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id, topic="   Photosynthesis  ")
        quiz = gen.json()
        assert quiz["topic"] == "Photosynthesis"

        answers = await _answers_for_quiz(quiz, select_correct=True)
        res = await _submit_with_answers(
            ac,
            token,
            quiz["id"],
            [
                {**a, "time_taken": 4.5}
                for a in answers
            ],
            started_at="2026-01-01T10:00:00Z",
        )
        assert res.status_code == 200

        attempts = await _db_attempts(user_id)
        assert attempts[0].started_at is not None

        saved_answers = await _db_answers_for(attempts[0].id)
        assert len(saved_answers) == len(answers)
        assert all(a.topic == "Photosynthesis" for a in saved_answers)
        assert all(a.difficulty == "medium" for a in saved_answers)
        assert all(a.time_taken == 4.5 for a in saved_answers)

        async with TestAsyncSessionLocal() as session:
            result = await session.execute(
                select(QuizQuestion).where(
                    QuizQuestion.quiz_id == uuid.UUID(quiz["id"])
                )
            )
            db_questions = list(result.scalars().all())
        assert len(db_questions) == len(quiz["questions"])
        assert all(q.topic == "Photosynthesis" for q in db_questions)


@pytest.mark.asyncio
async def test_performance_endpoint_returns_summary_attempts_and_mastery():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "intel_report@example.com")
        subject_id = await _create_subject(ac, token, "Report Subject")
        await _seed_chunks(user_id, subject_id, "Intel Ch3.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id, topic="Photosynthesis")
        quiz = gen.json()

        answers = await _answers_for_quiz(quiz, select_correct=True)
        answers[0]["selected_option"] = "A" if answers[0]["selected_option"] != "A" else "B"
        res = await _submit_with_answers(ac, token, quiz["id"], answers)
        assert res.status_code == 200
        assert res.json()["score"] == len(answers) - 1

        resp = await _performance(ac, token, subject_id)
        assert resp.status_code == 200
        data = resp.json()

        assert data["subject_id"] == subject_id
        summary = data["summary"]
        assert summary["total_attempts"] == 1
        assert summary["total_questions"] == len(answers)
        assert summary["total_correct"] == len(answers) - 1
        assert summary["total_incorrect"] == 1
        assert summary["best_percentage"] == summary["average_percentage"]
        assert len(data["attempts"]) == 1
        assert data["attempts"][0]["quiz_id"] == quiz["id"]
        assert data["attempts"][0]["score"] == len(answers) - 1

        assert any(b["difficulty"] == "medium" for b in summary["difficulty_breakdown"])
        breakdown = next(
            b for b in summary["difficulty_breakdown"] if b["difficulty"] == "medium"
        )
        assert breakdown["attempted"] == len(answers)
        assert breakdown["correct"] == len(answers) - 1

        assert len(data["mastery"]) == 1
        mastery = data["mastery"][0]
        assert mastery["topic"] == "Photosynthesis"
        assert mastery["attempts"] == 1
        assert mastery["questions_attempted"] == len(answers)
        assert mastery["questions_correct"] == len(answers) - 1
        assert mastery["questions_incorrect"] == 1
        assert mastery["accuracy"] == pytest.approx(
            round((len(answers) - 1) / len(answers) * 100, 1)
        )
        assert mastery["mastery_level"] == "developing"


@pytest.mark.asyncio
async def test_mastery_is_confidence_aware_and_accumulates():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "intel_mastery@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, "Intel Ch4.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id, topic="Photosynthesis", question_count=5)
        quiz = gen.json()
        answers = await _answers_for_quiz(quiz, select_correct=True)

        res = await _submit_with_answers(ac, token, quiz["id"], answers)
        assert res.status_code == 200

        row = await _db_mastery(user_id, subject_id, "Photosynthesis")
        assert row.attempts == 1
        assert row.questions_attempted == 5
        assert row.questions_correct == 5
        assert row.accuracy == 100.0
        assert row.mastery_score == 50.0  # 5 of 10 evidence questions → 50% confidence

        res = await _submit_with_answers(ac, token, quiz["id"], answers)
        assert res.status_code == 200

        row = await _db_mastery(user_id, subject_id, "Photosynthesis")
        assert row.attempts == 2
        assert row.questions_attempted == 10
        assert row.questions_correct == 10
        assert row.accuracy == 100.0
        assert row.mastery_score == 100.0  # full confidence + perfect accuracy
        assert row.mastery_level == "mastered"


@pytest.mark.asyncio
async def test_mastery_drops_with_incorrect_answers_and_untagged_uses_general():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "intel_incorrect@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, "Intel Ch5.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id, question_count=5)
        quiz = gen.json()

        wrong_answers = await _answers_for_quiz(quiz, select_correct=False)
        res = await _submit_with_answers(ac, token, quiz["id"], wrong_answers)
        assert res.status_code == 200

        row = await _db_mastery(user_id, subject_id, "General")
        assert row is not None
        assert row.questions_attempted == 5
        assert row.questions_correct == 0
        assert row.accuracy == 0.0
        assert row.mastery_score == 0.0

        all_correct = await _answers_for_quiz(quiz, select_correct=True)
        res = await _submit_with_answers(ac, token, quiz["id"], all_correct)
        assert res.status_code == 200

        row = await _db_mastery(user_id, subject_id, "General")
        assert row.questions_attempted == 10
        assert row.questions_correct == 5
        assert row.accuracy == 50.0
        assert row.mastery_score == 50.0  # full confidence, 50% accuracy


@pytest.mark.asyncio
async def test_attempts_and_performance_are_user_scoped():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "intel_owner@example.com")
        token_b, user_b = await _register(ac, "intel_intruder@example.com")

        subject_a = await _create_subject(ac, token_a, "Owner Subject")
        await _seed_chunks(user_a, subject_a, "Notes A.pdf", PASS_TEXTS)
        gen = await _generate(ac, token_a, subject_a)
        quiz = gen.json()
        answers = await _answers_for_quiz(quiz, select_correct=True)
        res = await _submit_with_answers(ac, token_a, quiz["id"], answers)
        assert res.status_code == 200

        attempts_a = await _db_attempts(user_a)
        attempts_b = await _db_attempts(user_b)
        assert len(attempts_a) == 1
        assert len(attempts_b) == 0

        intruder = await _performance(ac, token_b, subject_a)
        assert intruder.status_code == 404
        assert "access denied" in intruder.json()["detail"].lower()

        unknown = await _performance(ac, token_a, str(uuid.uuid4()))
        assert unknown.status_code == 404

        subject_b = await _create_subject(ac, token_b, "B Subject")
        empty = await _performance(ac, token_b, subject_b)
        assert empty.status_code == 200
        data = empty.json()
        assert data["summary"]["total_attempts"] == 0
        assert data["attempts"] == []
        assert data["mastery"] == []