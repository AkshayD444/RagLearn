import uuid
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


async def _create_subject(ac: AsyncClient, token: str, title: str = "Soft Delete Subject"):
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


async def _submit_all_correct(ac: AsyncClient, token: str, quiz: dict):
    answers = []
    for question in quiz["questions"]:
        db_q = await _find_db_question(question["id"])
        answers.append(
            {"question_id": question["id"], "selected_option": db_q.correct_option}
        )
    res = await ac.post(
        f"/api/v1/quizzes/{quiz['id']}/submit",
        json={"answers": answers},
        headers=_auth(token),
    )
    assert res.status_code == 200
    return res.json()


@pytest.fixture(autouse=True)
def _clear_db_cache():
    _db_cache.clear()


async def _db_subject(subject_id: str):
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(Subject).where(Subject.id == uuid.UUID(subject_id))
        )
        return result.scalar_one_or_none()


async def _db_quiz(quiz_id: str):
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(Quiz).where(Quiz.id == uuid.UUID(quiz_id))
        )
        return result.scalar_one_or_none()


async def _db_attempts_for(subject_id: str, user_id: str) -> list:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(QuizAttempt).where(
                QuizAttempt.subject_id == uuid.UUID(subject_id),
                QuizAttempt.user_id == uuid.UUID(user_id),
            )
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


PASS_TEXTS = [
    "Photosynthesis converts sunlight into chemical energy stored in glucose molecules within plant leaves.",
    "Chloroplasts contain chlorophyll pigment that absorbs sunlight and drives a reaction stage inside the cell.",
]


@pytest.mark.asyncio
async def test_delete_subject_preserves_attempts_mastery_and_history_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "soft_delete_subject@example.com")
        subject_id = await _create_subject(ac, token, "Chemistry 101")
        await _seed_chunks(user_id, subject_id, "Chem Ch1.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id, topic="Thermodynamics")
        assert gen.status_code == 201
        quiz = gen.json()
        result = await _submit_all_correct(ac, token, quiz)

        assert len(await _db_attempts_for(subject_id, user_id)) == 1
        mastery = await _db_mastery(user_id, subject_id, "Thermodynamics")
        assert mastery is not None

        res = await ac.delete(f"/api/v1/subjects/{subject_id}", headers=_auth(token))
        assert res.status_code == 204

        subject = await _db_subject(subject_id)
        assert subject is not None
        assert subject.deleted_at is not None

        attempts = await _db_attempts_for(subject_id, user_id)
        assert len(attempts) == 1
        assert attempts[0].score == result["score"]

        async with TestAsyncSessionLocal() as session:
            answer_result = await session.execute(
                select(QuizAttemptAnswer).where(
                    QuizAttemptAnswer.attempt_id == attempts[0].id
                )
            )
            saved_answers = list(answer_result.scalars().all())
        assert len(saved_answers) == result["total_questions"]

        preserved_mastery = await _db_mastery(user_id, subject_id, "Thermodynamics")
        assert preserved_mastery is not None
        assert preserved_mastery.questions_correct == result["score"]

        listed = await ac.get("/api/v1/subjects", headers=_auth(token))
        assert listed.status_code == 200
        assert all(s["id"] != subject_id for s in listed.json())

        fetched = await ac.get(f"/api/v1/subjects/{subject_id}", headers=_auth(token))
        assert fetched.status_code == 404

        history = await ac.get(
            f"/api/v1/analytics/subjects/{subject_id}/performance",
            headers=_auth(token),
        )
        assert history.status_code == 200
        data = history.json()
        assert data["summary"]["total_attempts"] == 1
        assert len(data["attempts"]) == 1
        assert len(data["mastery"]) == 1

        regen = await _generate(ac, token, subject_id)
        assert regen.status_code == 404

        upload = await ac.post(
            "/api/v1/documents/upload",
            data={"subject_id": subject_id},
            files={"file": ("notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
            headers=_auth(token),
        )
        assert upload.status_code == 404

        rag = await ac.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject_id, "question": "What is photosynthesis?"},
            headers=_auth(token),
        )
        assert rag.status_code == 404


@pytest.mark.asyncio
async def test_delete_quiz_preserves_attempts_and_blocks_resubmission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "soft_delete_quiz@example.com")
        subject_id = await _create_subject(ac, token, "Physics 101")
        await _seed_chunks(user_id, subject_id, "Phys Ch1.pdf", PASS_TEXTS)

        gen = await _generate(ac, token, subject_id)
        assert gen.status_code == 201
        quiz = gen.json()
        result = await _submit_all_correct(ac, token, quiz)

        res = await ac.delete(f"/api/v1/quizzes/{quiz['id']}", headers=_auth(token))
        assert res.status_code == 204

        db_quiz = await _db_quiz(quiz["id"])
        assert db_quiz is not None
        assert db_quiz.deleted_at is not None

        attempts = await _db_attempts_for(subject_id, user_id)
        assert len(attempts) == 1
        assert attempts[0].quiz_id == uuid.UUID(quiz["id"])
        assert attempts[0].score == result["score"]

        answers = []
        for question in quiz["questions"]:
            db_q = await _find_db_question(question["id"])
            answers.append(
                {"question_id": question["id"], "selected_option": db_q.correct_option}
            )
        resubmit = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": answers},
            headers=_auth(token),
        )
        assert resubmit.status_code == 404

        again = await ac.delete(f"/api/v1/quizzes/{quiz['id']}", headers=_auth(token))
        assert again.status_code == 404

        # Subject remains active; a fresh quiz can still be generated and submitted
        fresh = await _generate(ac, token, subject_id)
        assert fresh.status_code == 201
        fresh_result = await _submit_all_correct(ac, token, fresh.json())
        assert fresh_result["attempt_id"]

        attempts = await _db_attempts_for(subject_id, user_id)
        assert len(attempts) == 2

        mastery = await _db_mastery(user_id, subject_id, "General")
        assert mastery.questions_attempted == 10
        assert mastery.questions_correct == 10


@pytest.mark.asyncio
async def test_delete_is_user_scoped():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "soft_delete_owner@example.com")
        token_b, _ = await _register(ac, "soft_delete_intruder@example.com")

        subject_a = await _create_subject(ac, token_a, "Owner Only")
        await _seed_chunks(user_a, subject_a, "Owner Ch1.pdf", PASS_TEXTS)
        gen = await _generate(ac, token_a, subject_a)
        quiz = gen.json()

        intruder_quiz = await ac.delete(
            f"/api/v1/quizzes/{quiz['id']}", headers=_auth(token_b)
        )
        assert intruder_quiz.status_code == 404

        intruder_subject = await ac.delete(
            f"/api/v1/subjects/{subject_a}", headers=_auth(token_b)
        )
        assert intruder_subject.status_code == 404

        db_quiz = await _db_quiz(quiz["id"])
        assert db_quiz.deleted_at is None
        db_subject = await _db_subject(subject_a)
        assert db_subject.deleted_at is None

        # Owner can still delete their own quiz
        res = await ac.delete(f"/api/v1/quizzes/{quiz['id']}", headers=_auth(token_a))
        assert res.status_code == 204