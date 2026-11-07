import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.models.quiz import Quiz, QuizQuestion
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


async def _create_subject(ac: AsyncClient, token: str, title: str = "Quiz Subject"):
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


async def _read_database_questions(quiz_id: str) -> list:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(QuizQuestion).where(QuizQuestion.quiz_id == uuid.UUID(quiz_id))
        )
        return list(result.scalars().all())


async def _answers_for_quiz(quiz, select_correct: bool):
    answers = []
    for question in quiz["questions"]:
        if select_correct:
            db_q = await _find_db_question(question["id"])
            option = db_q.correct_option
        else:
            db_q = await _find_db_question(question["id"])
            for letter in ("A", "B", "C", "D"):
                if letter != db_q.correct_option:
                    option = letter
                    break
        answers.append({"question_id": question["id"], "selected_option": option})
    return answers


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


@pytest.fixture(autouse=True)
def _clear_db_cache():
    _db_cache.clear()


BIO_TEXTS = [
    "Photosynthesis converts sunlight into chemical energy stored in glucose molecules within plant leaves.",
    "Chloroplasts contain chlorophyll pigment that absorbs sunlight and drives a reaction stage inside the cell.",
]


@pytest.mark.asyncio
async def test_quiz_generate_returns_grounded_questions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "quiz_gen@example.com")
        subject_id = await _create_subject(ac, token, "Biology 101")
        await _seed_chunks(user_id, subject_id, "Bio Ch1.pdf", BIO_TEXTS)

        res = await _generate(ac, token, subject_id)
        assert res.status_code == 201
        data = res.json()

        assert data["subject_id"] == subject_id
        assert data["title"] == "Biology 101 Quiz"
        assert data["difficulty"] == "medium"
        assert data["question_count"] == 5
        assert data["question_count"] == len(data["questions"])
        assert data["created_at"]

        for q in data["questions"]:
            assert q["question_text"].strip()
            for letter in ("option_a", "option_b", "option_c", "option_d"):
                assert q[letter].strip()
            assert q["difficulty"] == "medium"
            assert q["source_document_title"] == "Bio Ch1.pdf"
            assert q["source_page_number"] >= 1
            assert q["source_snippet"]

        # Correct answer and explanation must NOT leak before submission
        assert "correct_option" not in res.text
        assert "explanation" not in res.text


@pytest.mark.asyncio
async def test_quiz_generate_honors_count_difficulty_topic_and_whitespace():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "quiz_opts@example.com")
        subject_id = await _create_subject(ac, token, "Biology 201")
        await _seed_chunks(user_id, subject_id, "Bio Ch2.pdf", BIO_TEXTS)

        res = await _generate(
            ac, token, subject_id, question_count=3, difficulty="hard", topic="   Photosynthesis  "
        )
        assert res.status_code == 201
        data = res.json()
        assert data["question_count"] == 3
        assert len(data["questions"]) == 3
        assert data["difficulty"] == "hard"
        assert all(q["difficulty"] == "hard" for q in data["questions"])
        assert data["topic"] == "Photosynthesis"


@pytest.mark.asyncio
async def test_quiz_generate_validation_errors(forget_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, _ = await _register(ac, "quiz_validate@example.com")
        subject_id = await _create_subject(ac, token, "Validation Subject")
        headers = _auth(token)

        # 401 without a token. B2: the client's cookie jar is cleared first,
        # because the session cookie now authenticates just as well as a header
        # and this assertion is about carrying no credential at all.
        forget_session(ac)
        no_token = await ac.post(
            "/api/v1/quizzes/generate", json={"subject_id": subject_id}
        )
        assert no_token.status_code == 401

        # 422 on bad difficulty
        bad_diff = await _generate(ac, token, subject_id, difficulty="impossible")
        assert bad_diff.status_code == 422

        # 422 on out-of-range question_count
        too_few = await _generate(ac, token, subject_id, question_count=0)
        assert too_few.status_code == 422
        too_many = await _generate(ac, token, subject_id, question_count=21)
        assert too_many.status_code == 422

        # 422 on missing body
        missing = await ac.post("/api/v1/quizzes/generate", headers=headers)
        assert missing.status_code == 422

        # 404 on unknown subject
        foreign = await _generate(ac, token, str(uuid.uuid4()))
        assert foreign.status_code == 404
        assert "access denied" in foreign.json()["detail"].lower()


@pytest.mark.asyncio
async def test_quiz_generate_empty_subject_is_400():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, _ = await _register(ac, "quiz_empty@example.com")
        subject_id = await _create_subject(ac, token, "Empty Subject")

        res = await _generate(ac, token, subject_id)
        assert res.status_code == 400
        assert "No indexed study materials" in res.json()["detail"]


@pytest.mark.asyncio
async def test_quiz_submit_perfect_score():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "quiz_pass@example.com")
        subject_id = await _create_subject(ac, token, "Biology 301")
        await _seed_chunks(user_id, subject_id, "Bio Ch3.pdf", BIO_TEXTS)

        gen = await _generate(ac, token, subject_id)
        assert gen.status_code == 201
        quiz = gen.json()

        answers = await _answers_for_quiz(quiz, select_correct=True)
        res = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": answers},
            headers=_auth(token),
        )
        assert res.status_code == 200
        data = res.json()

        assert data["quiz_id"] == quiz["id"]
        assert data["total_questions"] == len(answers)
        assert data["score"] == data["total_questions"]
        assert data["percentage"] == 100.0
        assert all(r["is_correct"] for r in data["results"])

        for result, answer in zip(data["results"], answers):
            assert result["question_id"] == answer["question_id"]
            assert result["correct_option"] == answer["selected_option"]
            assert result["explanation"].strip()
            assert result["source_document_title"] == "Bio Ch3.pdf"
            assert result["source_snippet"]


@pytest.mark.asyncio
async def test_quiz_submit_incorrect_answers_scores_zero():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "quiz_fail@example.com")
        subject_id = await _create_subject(ac, token, "Biology 401")
        await _seed_chunks(user_id, subject_id, "Bio Ch4.pdf", BIO_TEXTS)

        gen = await _generate(ac, token, subject_id)
        quiz = gen.json()
        answers = await _answers_for_quiz(quiz, select_correct=False)

        res = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": answers},
            headers=_auth(token),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["score"] == 0
        assert data["percentage"] == 0.0
        assert all(not r["is_correct"] for r in data["results"])

        # Wrong answers still reveal the correct one
        for result, answer in zip(data["results"], answers):
            assert result["selected_option"] == answer["selected_option"]
            assert result["correct_option"] != result["selected_option"]
            assert result["explanation"].strip()


@pytest.mark.asyncio
async def test_quiz_submit_validation_and_scoping_errors():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "quiz_subval@example.com")
        token_b, _ = await _register(ac, "quiz_intruder@example.com")

        subject_a = await _create_subject(ac, token_a, "Owner Subject")
        await _seed_chunks(user_a, subject_a, "Notes A.pdf", BIO_TEXTS)
        gen = await _generate(ac, token_a, subject_a)
        quiz = gen.json()
        questions = quiz["questions"]

        headers_a = _auth(token_a)

        # 422: empty answers list
        empty = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit", json={"answers": []}, headers=headers_a
        )
        assert empty.status_code == 422

        # 422: invalid option letter
        bad_opt = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": [
                {"question_id": q["id"], "selected_option": "E"} for q in questions
            ]},
            headers=headers_a,
        )
        assert bad_opt.status_code == 422

        # 400: duplicate answers for a question
        first = questions[0]["id"]
        duplicate = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={
                "answers": [
                    {"question_id": first, "selected_option": "A"},
                    {"question_id": first, "selected_option": "B"},
                ]
            },
            headers=headers_a,
        )
        assert duplicate.status_code == 400
        assert "not allowed" in duplicate.json()["detail"]

        # 400: foreign question id in payload
        foreign_q = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={
                "answers": [
                    {"question_id": str(uuid.uuid4()), "selected_option": "A"},
                    {"question_id": questions[1]["id"], "selected_option": "A"},
                ]
            },
            headers=headers_a,
        )
        assert foreign_q.status_code == 400

        # 400: partial answer set (missing questions)
        partial = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": [
                {"question_id": questions[0]["id"], "selected_option": "A"}
            ]},
            headers=headers_a,
        )
        assert partial.status_code == 400

        # 404: unknown quiz id
        unknown = await ac.post(
            f"/api/v1/quizzes/{uuid.uuid4()}/submit",
            json={"answers": [{"question_id": questions[0]["id"], "selected_option": "A"}]},
            headers=headers_a,
        )
        assert unknown.status_code == 404

        # 404: intruder cannot submit the owner's quiz
        intruder = await ac.post(
            f"/api/v1/quizzes/{quiz['id']}/submit",
            json={"answers": [
                {"question_id": q["id"], "selected_option": "A"} for q in questions
            ]},
            headers=_auth(token_b),
        )
        assert intruder.status_code == 404
        assert "access denied" in intruder.json()["detail"].lower()


@pytest.mark.asyncio
async def test_quiz_generate_is_user_scoped():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "quiz_owner@example.com")
        token_b, _ = await _register(ac, "quiz_other@example.com")

        subject_a = await _create_subject(ac, token_a, "Private Subject A")
        await _seed_chunks(user_a, subject_a, "Private Notes.pdf", BIO_TEXTS)

        # Intruder cannot generate from the owner's subject
        res = await _generate(ac, token_b, subject_a)
        assert res.status_code == 404
        assert "access denied" in res.json()["detail"].lower()