"""Quiz and attempt history: ownership, pagination, and per-quiz aggregates.

These tests seed the rows directly instead of driving the generation pipeline,
because the behaviour under test is the read path over existing history.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.quiz import Quiz, QuizQuestion
from tests.conftest import TestAsyncSessionLocal


async def _seed_quiz(
    user_id: str,
    subject_id: str,
    *,
    title: str = "History Quiz",
    difficulty: str = "medium",
    questions: int = 2,
    created_at: datetime | None = None,
    deleted: bool = False,
) -> str:
    """Inserts an owned quiz with its questions and returns the quiz id."""

    async with TestAsyncSessionLocal() as session:
        quiz = Quiz(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title=title,
            difficulty=difficulty,
            question_count=questions,
            topic="Retrieval",
        )
        if created_at is not None:
            quiz.created_at = created_at
        if deleted:
            quiz.deleted_at = datetime.utcnow()
        session.add(quiz)
        await session.flush()

        for i in range(questions):
            session.add(
                QuizQuestion(
                    quiz_id=quiz.id,
                    question_text=f"Question {i + 1}?",
                    option_a="A",
                    option_b="B",
                    option_c="C",
                    option_d="D",
                    correct_option="A",
                    explanation="Because.",
                    difficulty=difficulty,
                    topic="Retrieval",
                    document_title="materials.pdf",
                    page_number=4,
                    source_snippet="A source passage that grounds this question.",
                )
            )
        await session.commit()
        return str(quiz.id)


async def _seed_attempt(
    user_id: str,
    subject_id: str,
    quiz_id: str,
    *,
    score: int = 1,
    total_questions: int = 2,
    percentage: float = 50.0,
    completed_at: datetime | None = None,
    with_answers: bool = True,
) -> str:
    """Inserts an owned attempt, optionally with its per-question answers."""

    async with TestAsyncSessionLocal() as session:
        attempt = QuizAttempt(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            quiz_id=uuid.UUID(quiz_id),
            score=score,
            total_questions=total_questions,
            percentage=percentage,
            started_at=datetime.utcnow(),
            completed_at=completed_at or datetime.utcnow(),
        )
        session.add(attempt)
        await session.flush()

        if with_answers:
            questions = (
                await session.execute(
                    select(QuizQuestion).where(QuizQuestion.quiz_id == uuid.UUID(quiz_id))
                )
            ).scalars().all()
            for question in questions:
                session.add(
                    QuizAttemptAnswer(
                        attempt_id=attempt.id,
                        question_id=question.id,
                        question_text=question.question_text,
                        selected_answer="A",
                        correct_answer="A",
                        is_correct=True,
                        difficulty=question.difficulty,
                        topic=question.topic,
                    )
                )
        await session.commit()
        return str(attempt.id)


@pytest.mark.asyncio
async def test_list_quizzes_returns_owned_history_with_aggregates(
    client, registered_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, title="Cardiology")
    other_quiz = await _seed_quiz(registered_user["user_id"], subject, title="Neurology")
    now = datetime.utcnow()
    await _seed_attempt(
        registered_user["user_id"], subject, quiz_id, percentage=40.0, completed_at=now
    )
    await _seed_attempt(
        registered_user["user_id"],
        subject,
        quiz_id,
        percentage=90.0,
        completed_at=now + timedelta(seconds=1),
    )

    res = await client.get("/api/v1/quizzes", headers=registered_user["headers"])
    assert res.status_code == 200
    body = res.json()

    assert body["total"] == 2
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["has_more"] is False
    assert {item["id"] for item in body["items"]} == {quiz_id, other_quiz}

    cardiology = next(i for i in body["items"] if i["id"] == quiz_id)
    assert cardiology["subject_title"]
    assert cardiology["attempt_count"] == 2
    assert cardiology["best_percentage"] == 90.0
    assert cardiology["last_attempt_at"]
    # The quiz with no attempts reports zeroes rather than being dropped.
    assert next(i for i in body["items"] if i["id"] == other_quiz)["attempt_count"] == 0


@pytest.mark.asyncio
async def test_list_quizzes_is_paginated_and_hides_other_users(
    client, registered_user, other_user, subject
):
    for i in range(5):
        await _seed_quiz(registered_user["user_id"], subject, title=f"Mine {i}")
    intruder_subject = await client.post(
        "/api/v1/subjects", json={"title": "Theirs"}, headers=other_user["headers"]
    )
    intruder_subject_id = intruder_subject.json()["id"]
    await _seed_quiz(other_user["user_id"], intruder_subject_id, title="Not Mine")

    first = await client.get(
        "/api/v1/quizzes", params={"limit": 2}, headers=registered_user["headers"]
    )
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["has_more"] is True

    last = await client.get(
        "/api/v1/quizzes",
        params={"limit": 2, "offset": 4},
        headers=registered_user["headers"],
    )
    assert last.json()["has_more"] is False
    assert len(last.json()["items"]) == 1

    # A page past the end is empty, not an error.
    beyond = await client.get(
        "/api/v1/quizzes",
        params={"limit": 2, "offset": 99},
        headers=registered_user["headers"],
    )
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 5

    assert all(
        item["title"].startswith("Mine") for item in first.json()["items"]
    )


@pytest.mark.asyncio
async def test_list_quizzes_validates_pagination_bounds(client, registered_user):
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        res = await client.get(
            "/api/v1/quizzes", params=params, headers=registered_user["headers"]
        )
        assert res.status_code == 422, params


@pytest.mark.asyncio
async def test_list_quizzes_filters_by_subject_and_requires_ownership(
    client, registered_user, other_user, subject
):
    other_subject = await client.post(
        "/api/v1/subjects", json={"title": "Chemistry"}, headers=registered_user["headers"]
    )
    other_subject_id = other_subject.json()["id"]
    await _seed_quiz(registered_user["user_id"], subject, title="Physics Quiz")
    await _seed_quiz(registered_user["user_id"], other_subject_id, title="Chem Quiz")

    scoped = await client.get(
        "/api/v1/quizzes",
        params={"subject_id": other_subject_id},
        headers=registered_user["headers"],
    )
    assert scoped.status_code == 200
    assert scoped.json()["total"] == 1
    assert scoped.json()["items"][0]["title"] == "Chem Quiz"

    foreign_subject = await client.post(
        "/api/v1/subjects", json={"title": "Foreign"}, headers=other_user["headers"]
    )
    denied = await client.get(
        "/api/v1/quizzes",
        params={"subject_id": foreign_subject.json()["id"]},
        headers=registered_user["headers"],
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_list_quizzes_requires_authentication(client):
    assert (await client.get("/api/v1/quizzes")).status_code == 401


@pytest.mark.asyncio
async def test_soft_deleted_quiz_leaves_the_quiz_history(
    client, registered_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, deleted=True)

    res = await client.get("/api/v1/quizzes", headers=registered_user["headers"])
    assert res.status_code == 200
    assert quiz_id not in {item["id"] for item in res.json()["items"]}

    detail = await client.get(
        f"/api/v1/quizzes/{quiz_id}", headers=registered_user["headers"]
    )
    assert detail.status_code == 404


@pytest.mark.asyncio
async def test_get_quiz_returns_questions_and_aggregates(
    client, registered_user, other_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, questions=3)
    await _seed_attempt(registered_user["user_id"], subject, quiz_id, percentage=66.7)

    res = await client.get(
        f"/api/v1/quizzes/{quiz_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["questions"]) == 3
    assert body["question_count"] == 3
    assert body["attempt_count"] == 1
    assert body["best_percentage"] == 66.7
    # The graded answer key must stay hidden until submission.
    assert "correct_option" not in res.text

    foreign = await client.get(
        f"/api/v1/quizzes/{quiz_id}", headers=other_user["headers"]
    )
    assert foreign.status_code == 404

    unknown = await client.get(
        f"/api/v1/quizzes/{uuid.uuid4()}", headers=registered_user["headers"]
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_list_quiz_attempts_is_paginated_and_joined(
    client, registered_user, other_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, title="Photosynthesis")
    base = datetime(2024, 1, 1, 12, 0, 0)
    ids = [
        await _seed_attempt(
            registered_user["user_id"],
            subject,
            quiz_id,
            percentage=20.0 * (i + 1),
            completed_at=base + timedelta(minutes=i),
            with_answers=False,
        )
        for i in range(3)
    ]

    res = await client.get(
        "/api/v1/quizzes/attempts",
        params={"limit": 2},
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    # Newest first.
    assert body["items"][0]["completed_at"] > body["items"][1]["completed_at"]

    entry = body["items"][0]
    assert entry["quiz_id"] == quiz_id
    assert entry["quiz_title"] == "Photosynthesis"
    assert entry["subject_title"]
    assert entry["percentage"] == 60.0

    # The newest row appears on page 1 and the oldest on page 2, with no overlap.
    second = await client.get(
        "/api/v1/quizzes/attempts",
        params={"limit": 2, "offset": 2},
        headers=registered_user["headers"],
    )
    assert second.json()["has_more"] is False
    assert {item["id"] for item in second.json()["items"]} == {ids[0]}


@pytest.mark.asyncio
async def test_list_quiz_attempts_excludes_other_users(client, registered_user, other_user):
    # A real owned subject and attempt for the intruder: any leak would show up.
    owned = await client.post(
        "/api/v1/subjects", json={"title": "Intruder Subject"}, headers=other_user["headers"]
    )
    intruder_quiz = await _seed_quiz(other_user["user_id"], owned.json()["id"])
    await _seed_attempt(other_user["user_id"], owned.json()["id"], intruder_quiz)

    res = await client.get(
        "/api/v1/quizzes/attempts", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json()["total"] == 0
    assert res.json()["items"] == []


@pytest.mark.asyncio
async def test_list_quiz_attempts_requires_authentication(client):
    assert (await client.get("/api/v1/quizzes/attempts")).status_code == 401


@pytest.mark.asyncio
async def test_get_quiz_attempt_returns_recorded_answers(
    client, registered_user, other_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, questions=2)
    attempt_id = await _seed_attempt(
        registered_user["user_id"], subject, quiz_id, score=2, percentage=100.0
    )

    res = await client.get(
        f"/api/v1/quizzes/attempts/{attempt_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == attempt_id
    assert body["score"] == 2
    assert body["percentage"] == 100.0
    assert len(body["answers"]) == 2
    for answer in body["answers"]:
        assert answer["question_text"].strip()
        assert answer["selected_answer"] == "A"
        assert answer["correct_answer"] == "A"
        assert answer["is_correct"] is True
        assert answer["difficulty"] == "medium"

    foreign = await client.get(
        f"/api/v1/quizzes/attempts/{attempt_id}", headers=other_user["headers"]
    )
    assert foreign.status_code == 404

    unknown = await client.get(
        f"/api/v1/quizzes/attempts/{uuid.uuid4()}", headers=registered_user["headers"]
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_get_quiz_attempt_enriches_answers_from_questions(
    client, registered_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, questions=2)
    attempt_id = await _seed_attempt(registered_user["user_id"], subject, quiz_id)

    res = await client.get(
        f"/api/v1/quizzes/attempts/{attempt_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["answers"]) == 2

    # The recorded letters stay the source of truth...
    for answer in body["answers"]:
        assert answer["selected_answer"] == "A"
        assert answer["correct_answer"] == "A"
        # ...and the persisted question fills in the review fields: the option
        # texts, the explanation, and the citation back to the source.
        assert answer["option_a"] == "A"
        assert answer["option_b"] == "B"
        assert answer["option_c"] == "C"
        assert answer["option_d"] == "D"
        assert answer["explanation"] == "Because."
        assert answer["source_document_title"] == "materials.pdf"
        assert answer["source_page_number"] == 4
        assert answer["source_snippet"]
    # Every answer belongs to the same topic, so the convenience field exposes it.
    assert body["topic"] == "Retrieval"


@pytest.mark.asyncio
async def test_get_quiz_attempt_hides_shared_topic_when_answers_span_topics(
    client, registered_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, questions=2)
    attempt_id = await _seed_attempt(registered_user["user_id"], subject, quiz_id)

    # Push one answer onto a different topic so the attempt no longer has a
    # single shared topic; the convenience field must then be null.
    async with TestAsyncSessionLocal() as session:
        answer = (
            await session.execute(
                select(QuizAttemptAnswer)
                .where(QuizAttemptAnswer.attempt_id == uuid.UUID(attempt_id))
                .limit(1)
            )
        ).scalars().first()
        answer.topic = "Ranking"
        await session.commit()

    res = await client.get(
        f"/api/v1/quizzes/attempts/{attempt_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json()["topic"] is None


@pytest.mark.asyncio
async def test_get_quiz_attempt_falls_back_when_question_is_gone(
    client, registered_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, questions=2)
    attempt_id = await _seed_attempt(registered_user["user_id"], subject, quiz_id)

    # Remove the question rows behind the answers (SQLite does not cascade, so
    # the recorded attempt answers survive). The detail must still read the
    # recorded answer and degrade the richer review fields to null instead of
    # failing.
    async with TestAsyncSessionLocal() as session:
        await session.execute(
            delete(QuizQuestion).where(QuizQuestion.quiz_id == uuid.UUID(quiz_id))
        )
        await session.commit()

    res = await client.get(
        f"/api/v1/quizzes/attempts/{attempt_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["answers"]) == 2
    for answer in body["answers"]:
        assert answer["question_text"]
        assert answer["selected_answer"] == "A"
        assert answer["correct_answer"] == "A"
        assert answer["is_correct"] is True
        assert answer["option_a"] is None
        assert answer["option_b"] is None
        assert answer["option_c"] is None
        assert answer["option_d"] is None
        assert answer["explanation"] is None
        assert answer["source_document_title"] is None
        assert answer["source_page_number"] is None
        assert answer["source_snippet"] is None


@pytest.mark.asyncio
async def test_attempts_of_a_deleted_quiz_remain_reviewable(
    client, registered_user, subject
):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject)
    attempt_id = await _seed_attempt(registered_user["user_id"], subject, quiz_id)

    deleted = await client.delete(
        f"/api/v1/quizzes/{quiz_id}", headers=registered_user["headers"]
    )
    assert deleted.status_code == 204

    listed = await client.get(
        "/api/v1/quizzes/attempts", headers=registered_user["headers"]
    )
    assert listed.json()["total"] == 1

    detail = await client.get(
        f"/api/v1/quizzes/attempts/{attempt_id}", headers=registered_user["headers"]
    )
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["answers"]) == 2
    # A soft-deleted quiz keeps its questions, so the review stays fully rich.
    for answer in body["answers"]:
        assert answer["explanation"] == "Because."
        assert answer["source_document_title"] == "materials.pdf"
        assert answer["source_page_number"] == 4
