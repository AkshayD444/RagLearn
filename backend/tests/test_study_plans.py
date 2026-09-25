import uuid
from datetime import date, datetime, timedelta
from typing import List, Tuple

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz, QuizQuestion
from app.models.study_plan import StudyPlan, StudyPlanItem, StudyPlanSession
from app.services.student_intelligence import compute_accuracy, compute_mastery, mastery_level
from tests.conftest import TestAsyncSessionLocal


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(ac: AsyncClient, email: str):
    response = await ac.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Plan Student", "password": "password123"},
    )
    assert response.status_code == 201
    data = response.json()
    return data["access_token"], data["user"]["id"]


async def _create_subject(ac: AsyncClient, token: str, title: str = "Planning Subject"):
    response = await ac.post(
        "/api/v1/subjects", json={"title": title}, headers=_auth(token)
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _seed_topic_history(
    user_id: str,
    subject_id: str,
    topic: str,
    outcomes: List[Tuple[bool, datetime]],
) -> None:
    correct = sum(1 for is_correct, _ in outcomes if is_correct)
    attempted = len(outcomes)
    completed_at = outcomes[-1][1]

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
        for index in range(attempted):
            question = QuizQuestion(
                quiz_id=quiz.id,
                question_text=f"Question {index + 1} for {topic}",
                option_a="Alpha",
                option_b="Beta",
                option_c="Gamma",
                option_d="Delta",
                correct_option="A",
                explanation="Seeded study-plan history.",
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
            percentage=round(correct / attempted * 100, 1),
            completed_at=completed_at,
        )
        session.add(attempt)
        await session.flush()

        for index, (is_correct, created_at) in enumerate(outcomes):
            session.add(
                QuizAttemptAnswer(
                    attempt_id=attempt.id,
                    question_id=questions[index].id,
                    question_text=questions[index].question_text,
                    selected_answer="A" if is_correct else "B",
                    correct_answer="A",
                    is_correct=is_correct,
                    difficulty="medium",
                    topic=topic,
                    created_at=created_at,
                )
            )

        mastery_score = compute_mastery(correct, attempted)
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
                last_attempt_at=completed_at,
            )
        )
        await session.commit()


async def _seed_material(user_id: str, subject_id: str) -> None:
    async with TestAsyncSessionLocal() as session:
        document = Document(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title="Planning Notes.pdf",
            file_path="",
            file_size_bytes=100,
            status="READY",
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                subject_id=uuid.UUID(subject_id),
                user_id=uuid.UUID(user_id),
                chunk_index=1,
                page_number=1,
                token_count=8,
                content="Seeded indexed planning material.",
                embedding=[0.0] * 768,
            )
        )
        await session.commit()


async def _generate(
    ac: AsyncClient,
    token: str,
    subject_id: str,
    **options,
):
    payload = {"subject_id": subject_id, **options}
    return await ac.post(
        "/api/v1/study-plans/generate",
        json=payload,
        headers=_auth(token),
    )


def _items(plan: dict) -> list:
    return [item for session in plan["sessions"] for item in session["items"]]


@pytest.mark.asyncio
async def test_study_plans_require_authentication():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        subject_id = str(uuid.uuid4())
        response = await _generate(ac, "", subject_id)
        assert response.status_code == 401

        latest = await ac.get(f"/api/v1/study-plans/subjects/{subject_id}")
        assert latest.status_code == 401

        item_id = str(uuid.uuid4())
        completed = await ac.patch(
            f"/api/v1/study-plans/items/{item_id}/complete", json={"completed": True}
        )
        assert completed.status_code == 401


@pytest.mark.asyncio
async def test_generated_plan_prioritizes_weak_topics_and_persists_sessions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_priority@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_material(user_id, subject_id)
        await _seed_topic_history(
            user_id,
            subject_id,
            "WeakTopic",
            [(False, now - timedelta(hours=1))] * 4,
        )
        await _seed_topic_history(
            user_id,
            subject_id,
            "StrongTopic",
            [(True, now - timedelta(hours=1))] * 4,
        )

        response = await _generate(
            ac,
            token,
            subject_id,
            requested_sessions=2,
            max_topics_per_session=1,
        )
        assert response.status_code == 201
        plan = response.json()
        assert plan["subject_id"] == subject_id
        assert plan["material_available"] is True
        assert [session["session_number"] for session in plan["sessions"]] == [1, 2]
        assert [item["topic"] for item in _items(plan)] == ["WeakTopic", "StrongTopic"]
        assert _items(plan)[0]["action"] == "REVIEW"
        assert "Repeated incorrect" in _items(plan)[0]["reason"]
        assert _items(plan)[1]["action"] == "MAINTAIN"
        assert all(session["estimated_minutes"] > 0 for session in plan["sessions"])

        latest = await ac.get(
            f"/api/v1/study-plans/subjects/{subject_id}",
            headers=_auth(token),
        )
        assert latest.status_code == 200
        assert latest.json()["id"] == plan["id"]

        async with TestAsyncSessionLocal() as session:
            stored = await session.execute(
                select(StudyPlan).where(StudyPlan.id == uuid.UUID(plan["id"]))
            )
            stored_plan = stored.scalar_one()
            item_count = await session.execute(
                select(StudyPlanItem).where(
                    StudyPlanItem.session_id.in_(
                        select(StudyPlanSession.id).where(
                            StudyPlanSession.plan_id == uuid.UUID(plan["id"])
                        )
                    )
                )
            )
            assert stored_plan.user_id == uuid.UUID(user_id)
            assert len(item_count.scalars().all()) == 2


@pytest.mark.asyncio
async def test_no_material_is_reported_without_fabricating_learning_content():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_no_material@example.com")
        subject_id = await _create_subject(ac, token, "Unmaterial Subject")
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id,
            subject_id,
            "UnstudiedMaterial",
            [(False, now - timedelta(minutes=30))] * 4,
        )

        response = await _generate(ac, token, subject_id)
        assert response.status_code == 201
        plan = response.json()
        item = _items(plan)[0]
        assert item["action"] == "LEARN"
        assert item["material_available"] is False
        assert "unavailable" in item["reason"].lower()
        assert "upload" in item["reason"].lower()
        assert "content" not in item["reason"].lower()


@pytest.mark.asyncio
async def test_single_failed_question_does_not_become_weak_review():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_one_attempt@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_material(user_id, subject_id)
        await _seed_topic_history(
            user_id,
            subject_id,
            "OneAttempt",
            [(False, now - timedelta(minutes=10))],
        )

        response = await _generate(ac, token, subject_id)
        item = response.json()["sessions"][0]["items"][0]
        assert item["action"] == "LEARN"
        assert item["priority"] < 40
        assert "not enough questions" in item["reason"].lower()


@pytest.mark.asyncio
async def test_thin_evidence_never_becomes_a_review_priority():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_thin_evidence@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_material(user_id, subject_id)
        await _seed_topic_history(
            user_id,
            subject_id,
            "ThinEvidence",
            [(False, now - timedelta(minutes=10)), (False, now - timedelta(minutes=5))],
        )

        response = await _generate(ac, token, subject_id)
        assert response.status_code == 201
        item = _items(response.json())[0]
        assert item["questions_attempted"] < 3
        assert item["action"] == "LEARN"
        assert "not enough questions" in item["reason"].lower()


@pytest.mark.asyncio
async def test_daily_capacity_must_fit_the_longest_planned_topic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_capacity@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_material(user_id, subject_id)
        for topic in ("FirstWeak", "SecondWeak"):
            await _seed_topic_history(
                user_id,
                subject_id,
                topic,
                [(False, now - timedelta(hours=1))] * 4,
            )

        rejected = await _generate(
            ac,
            token,
            subject_id,
            available_minutes_per_day=10,
            requested_sessions=2,
        )
        assert rejected.status_code == 400
        assert "at least 15 minutes" in rejected.json()["detail"].lower()

        accepted = await _generate(
            ac,
            token,
            subject_id,
            available_minutes_per_day=15,
            requested_sessions=2,
        )
        assert accepted.status_code == 201
        plan = accepted.json()
        assert len(plan["sessions"]) == 2
        assert all(
            session["estimated_minutes"] <= 15 for session in plan["sessions"]
        )
        assert plan["omitted_topic_count"] == 0


@pytest.mark.asyncio
async def test_target_date_schedules_only_inside_window():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_target_date@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        for topic in ("HighPriority", "SecondPriority", "ThirdPriority"):
            await _seed_topic_history(
                user_id,
                subject_id,
                topic,
                [(False, now - timedelta(hours=1))] * 4,
            )
        target_date = date.today() + timedelta(days=3)

        response = await _generate(
            ac,
            token,
            subject_id,
            target_date=target_date.isoformat(),
            requested_sessions=2,
            max_topics_per_session=1,
        )
        assert response.status_code == 201
        plan = response.json()
        assert plan["target_date"] == target_date.isoformat()
        for session in plan["sessions"]:
            assert session["scheduled_date"] <= target_date.isoformat()
        assert plan["sessions"][-1]["scheduled_date"] == target_date.isoformat()
        assert _items(plan)[0]["priority"] >= _items(plan)[1]["priority"]


@pytest.mark.asyncio
async def test_invalid_target_date_is_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_invalid_date@example.com")
        subject_id = await _create_subject(ac, token)
        past_date = date.today() - timedelta(days=1)

        response = await _generate(
            ac, token, subject_id, target_date=past_date.isoformat()
        )
        assert response.status_code == 400
        assert "target date" in response.json()["detail"].lower()

        invalid_format = await _generate(
            ac, token, subject_id, target_date="not-a-date"
        )
        assert invalid_format.status_code == 422


@pytest.mark.asyncio
async def test_plan_output_is_deterministic_for_same_data_and_preferences():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_determinism@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        for topic, outcomes in (
            ("FirstWeak", [(False, now)] * 4),
            ("SecondWeak", [(False, now)] * 3 + [(True, now)]),
            ("Strong", [(True, now)] * 4),
        ):
            await _seed_topic_history(user_id, subject_id, topic, outcomes)

        options = {
            "requested_sessions": 2,
            "max_topics_per_session": 2,
            "available_minutes_per_day": 30,
        }
        first = (await _generate(ac, token, subject_id, **options)).json()
        second = (await _generate(ac, token, subject_id, **options)).json()

        def normalized(plan):
            return [
                [
                    (
                        session["session_number"],
                        session["scheduled_date"],
                        [
                            (
                                item["position"] if "position" in item else index,
                                item["topic"],
                                item["action"],
                                item["priority"],
                                item["reason"],
                            )
                            for index, item in enumerate(session["items"], start=1)
                        ],
                    )
                    for session in plan["sessions"]
                ]
            ]

        assert normalized(first) == normalized(second)
        assert [item["topic"] for item in _items(first)] == [
            item["topic"] for item in _items(second)
        ]


@pytest.mark.asyncio
async def test_deleted_subject_is_excluded_from_generation_and_retrieval():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "plan_deleted_subject@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id,
            subject_id,
            "DeletedTopic",
            [(False, now - timedelta(hours=1))] * 4,
        )
        first = await _generate(ac, token, subject_id)
        assert first.status_code == 201
        item_id = _items(first.json())[0]["id"]

        deleted = await ac.delete(
            f"/api/v1/subjects/{subject_id}", headers=_auth(token)
        )
        assert deleted.status_code == 204

        generation = await _generate(ac, token, subject_id)
        assert generation.status_code == 404
        latest = await ac.get(
            f"/api/v1/study-plans/subjects/{subject_id}", headers=_auth(token)
        )
        assert latest.status_code == 404
        completion = await ac.patch(
            f"/api/v1/study-plans/items/{item_id}/complete",
            json={"completed": True},
            headers=_auth(token),
        )
        assert completion.status_code == 404


@pytest.mark.asyncio
async def test_completion_is_user_scoped_and_does_not_change_mastery():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "plan_completion_owner@example.com")
        token_b, _ = await _register(ac, "plan_completion_intruder@example.com")
        subject_id = await _create_subject(ac, token_a, "Completion Subject")
        now = datetime.utcnow()
        await _seed_topic_history(
            user_a,
            subject_id,
            "CompletionTopic",
            [(False, now - timedelta(hours=1))] * 4,
        )
        generated = await _generate(ac, token_a, subject_id)
        item = _items(generated.json())[0]

        async with TestAsyncSessionLocal() as session:
            before = await session.execute(
                select(TopicMastery).where(
                    TopicMastery.user_id == uuid.UUID(user_a),
                    TopicMastery.subject_id == uuid.UUID(subject_id),
                    TopicMastery.topic == "CompletionTopic",
                )
            )
            before_mastery = before.scalar_one()

        foreign = await ac.patch(
            f"/api/v1/study-plans/items/{item['id']}/complete",
            json={"completed": True},
            headers=_auth(token_b),
        )
        assert foreign.status_code == 404

        completed = await ac.patch(
            f"/api/v1/study-plans/items/{item['id']}/complete",
            json={"completed": True},
            headers=_auth(token_a),
        )
        assert completed.status_code == 200
        completed_item = next(
            candidate
            for candidate in _items(completed.json())
            if candidate["id"] == item["id"]
        )
        assert completed_item["completed"] is True
        assert completed_item["completed_at"] is not None

        async with TestAsyncSessionLocal() as session:
            after = await session.execute(
                select(TopicMastery).where(
                    TopicMastery.user_id == uuid.UUID(user_a),
                    TopicMastery.subject_id == uuid.UUID(subject_id),
                    TopicMastery.topic == "CompletionTopic",
                )
            )
            after_mastery = after.scalar_one()
            assert after_mastery.mastery_score == before_mastery.mastery_score
            assert after_mastery.questions_attempted == before_mastery.questions_attempted


@pytest.mark.asyncio
async def test_only_authenticated_users_plans_are_retrievable():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "plan_latest_owner@example.com")
        token_b, _ = await _register(ac, "plan_latest_intruder@example.com")
        subject_id = await _create_subject(ac, token_a, "Latest Subject")
        now = datetime.utcnow()
        await _seed_topic_history(
            user_a,
            subject_id,
            "OwnerTopic",
            [(False, now - timedelta(hours=1))] * 4,
        )
        generated = await _generate(ac, token_a, subject_id)
        assert generated.status_code == 201

        own_latest = await ac.get(
            "/api/v1/study-plans/latest", headers=_auth(token_a)
        )
        assert own_latest.status_code == 200
        assert own_latest.json()["id"] == generated.json()["id"]

        intruder_latest = await ac.get(
            "/api/v1/study-plans/latest", headers=_auth(token_b)
        )
        assert intruder_latest.status_code == 404

        intruder_subject = await ac.get(
            f"/api/v1/study-plans/subjects/{subject_id}",
            headers=_auth(token_b),
        )
        assert intruder_subject.status_code == 404
