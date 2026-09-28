"""Cross-subject analytics overview and the bounded `/performance` summary.

The behaviour under test is that totals are computed by the database. A student
with more history than a single response can hold still gets correct totals, and
the returned lists stay capped. ``test_totals_ignore_the_page_limit`` is the
regression guard: if the summary ever starts being derived from the returned
rows instead of an aggregate, that test fails.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.models.attempt import QuizAttempt
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.flashcard import Flashcard, FlashcardDeck
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz
from app.rag.embeddings import embedding_service
from tests.conftest import TestAsyncSessionLocal, test_engine


async def _seed_quiz(user_id: str, subject_id: str, title: str) -> str:
    async with TestAsyncSessionLocal() as session:
        quiz = Quiz(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title=title,
            difficulty="medium",
            question_count=2,
        )
        session.add(quiz)
        await session.commit()
        return str(quiz.id)


async def _seed_attempt(
    user_id: str,
    subject_id: str,
    quiz_id: str,
    *,
    score: int,
    total_questions: int = 2,
    completed_at: datetime,
) -> str:
    async with TestAsyncSessionLocal() as session:
        attempt = QuizAttempt(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            quiz_id=uuid.UUID(quiz_id),
            score=score,
            total_questions=total_questions,
            percentage=round(100 * score / total_questions, 1),
            started_at=completed_at - timedelta(minutes=5),
            completed_at=completed_at,
        )
        session.add(attempt)
        await session.commit()
        return str(attempt.id)


async def _seed_mastery(
    user_id: str, subject_id: str, topic: str, mastery_score: float
) -> None:
    async with TestAsyncSessionLocal() as session:
        session.add(
            TopicMastery(
                user_id=uuid.UUID(user_id),
                subject_id=uuid.UUID(subject_id),
                topic=topic,
                mastery_score=mastery_score,
                mastery_level="developing" if mastery_score < 0.5 else "proficient",
                accuracy=mastery_score * 100,
                attempts=2,
                questions_attempted=4,
                questions_correct=int(4 * mastery_score),
                questions_incorrect=4 - int(4 * mastery_score),
            )
        )
        await session.commit()


async def _seed_due_flashcards(user_id: str, subject_id: str, count: int = 2) -> None:
    """Seeds a deck of never-studied cards, which are all due immediately."""
    async with TestAsyncSessionLocal() as session:
        deck = FlashcardDeck(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title="Overview Deck",
            card_count=count,
        )
        session.add(deck)
        await session.flush()
        for i in range(count):
            session.add(
                Flashcard(
                    deck_id=deck.id,
                    front_text=f"Front {i}",
                    back_text=f"Back {i}",
                    position=i,
                )
            )
        await session.commit()


def _record_statements():
    """Captures every statement the test engine runs while the block is open."""

    statements: list[str] = []

    def on_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", on_cursor_execute)
    return statements, lambda: event.remove(
        test_engine.sync_engine, "before_cursor_execute", on_cursor_execute
    )


@pytest.mark.asyncio
async def test_overview_aggregates_across_subjects(client, registered_user, subject):
    second = await client.post(
        "/api/v1/subjects", json={"title": "Microeconomics"}, headers=registered_user["headers"]
    )
    second_id = second.json()["id"]

    base = datetime(2024, 5, 1, 9, 0, 0)
    bio_quiz = await _seed_quiz(registered_user["user_id"], subject, "Anatomy Quiz")
    econ_quiz = await _seed_quiz(registered_user["user_id"], second_id, "Supply Quiz")
    await _seed_attempt(
        registered_user["user_id"], subject, bio_quiz, score=1, completed_at=base
    )
    await _seed_attempt(
        registered_user["user_id"], subject, bio_quiz, score=2, completed_at=base + timedelta(hours=1)
    )
    await _seed_attempt(
        registered_user["user_id"], second_id, econ_quiz, score=0, completed_at=base + timedelta(hours=2)
    )
    await _seed_mastery(registered_user["user_id"], subject, "Circulation", 0.4)
    await _seed_mastery(registered_user["user_id"], subject, "Nervous System", 0.8)

    res = await client.get(
        "/api/v1/analytics/overview", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    body = res.json()

    totals = body["totals"]
    assert totals["subject_count"] == 2
    assert totals["quiz_count"] == 2
    assert totals["attempt_count"] == 3
    assert totals["questions_answered"] == 6
    assert totals["questions_correct"] == 3
    assert totals["questions_incorrect"] == 3
    assert totals["accuracy"] == 50.0
    assert totals["average_percentage"] == 50.0
    assert totals["best_percentage"] == 100.0
    assert totals["first_activity_at"]
    assert totals["last_activity_at"]

    # Timeline is newest first and carries the titles needed to render it.
    timeline = body["timeline"]
    assert [entry["quiz_title"] for entry in timeline] == [
        "Supply Quiz",
        "Anatomy Quiz",
        "Anatomy Quiz",
    ]
    assert timeline[0]["subject_title"] == "Microeconomics"
    assert timeline[0]["percentage"] == 0.0

    subjects = body["subjects"]
    assert len(subjects) == 2
    assert subjects[0]["subject_title"] == "Microeconomics"
    assert subjects[0]["attempt_count"] == 1
    assert subjects[1]["attempt_count"] == 2
    assert subjects[1]["best_percentage"] == 100.0

    weakest = body["weakest_topics"]
    assert [t["topic"] for t in weakest] == ["Circulation", "Nervous System"]


@pytest.mark.asyncio
async def test_overview_is_empty_for_a_new_student(client, registered_user):
    res = await client.get(
        "/api/v1/analytics/overview", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    body = res.json()
    assert body["totals"] == {
        "subject_count": 0,
        "quiz_count": 0,
        "attempt_count": 0,
        "questions_answered": 0,
        "questions_correct": 0,
        "questions_incorrect": 0,
        "accuracy": 0.0,
        "average_percentage": 0.0,
        "best_percentage": 0.0,
        "document_count": 0,
        "ready_document_count": 0,
        "chunk_count": 0,
        "due_flashcard_count": 0,
        "average_topic_mastery": 0.0,
        "first_activity_at": None,
        "last_activity_at": None,
    }
    assert body["timeline"] == []
    assert body["subjects"] == []
    assert body["weakest_topics"] == []


@pytest.mark.asyncio
async def test_totals_ignore_the_page_limit(client, registered_user, subject):
    """The headline numbers describe the whole history, not the page returned."""

    quiz_id = await _seed_quiz(registered_user["user_id"], subject, "Long History")
    base = datetime(2024, 6, 1, 8, 0, 0)
    for i in range(12):
        await _seed_attempt(
            registered_user["user_id"],
            subject,
            quiz_id,
            score=i % 2,
            completed_at=base + timedelta(hours=i),
        )

    res = await client.get(
        "/api/v1/analytics/overview",
        params={"limit": 3},
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["timeline"]) == 3
    assert len(body["subjects"]) == 1
    assert body["totals"]["attempt_count"] == 12
    assert body["totals"]["questions_answered"] == 24
    assert body["totals"]["best_percentage"] == 50.0


@pytest.mark.asyncio
async def test_overview_lists_are_capped_and_queries_are_limited(
    client, registered_user, subject
):
    quiz_ids = [
        await _seed_quiz(registered_user["user_id"], subject, f"Quiz {i}")
        for i in range(6)
    ]
    base = datetime(2024, 7, 1, 8, 0, 0)
    for i, quiz_id in enumerate(quiz_ids):
        await _seed_attempt(
            registered_user["user_id"],
            subject,
            quiz_id,
            score=1,
            completed_at=base + timedelta(minutes=i),
        )
    for i in range(6):
        await _seed_mastery(registered_user["user_id"], subject, f"Topic {i}", 0.1 * (i + 1))

    statements, stop = _record_statements()
    try:
        res = await client.get(
            "/api/v1/analytics/overview",
            params={"limit": 2},
            headers=registered_user["headers"],
        )
    finally:
        stop()

    assert res.status_code == 200
    body = res.json()
    assert len(body["timeline"]) == 2
    assert len(body["subjects"]) == 1
    assert len(body["weakest_topics"]) == 2
    assert body["totals"]["attempt_count"] == 6

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert selects, "expected the overview to run SQL"

    # A query over these tables is only acceptable if it cannot return one row
    # per attempt/mastery/quiz: it must group, aggregate to a single row, or
    # cap itself with a LIMIT.
    single_row_markers = ("GROUP BY", "LIMIT", "COUNT(", "SUM(", "AVG(", "MAX(", "MIN(")
    unbounded = [
        s
        for s in selects
        if any(table in s for table in ("quiz_attempts", "topic_mastery", "quizzes"))
        and not any(marker in s.upper() for marker in single_row_markers)
    ]
    assert not unbounded, unbounded


@pytest.mark.asyncio
async def test_overview_validates_its_limit(client, registered_user):
    for params in ({"limit": 0}, {"limit": 51}):
        res = await client.get(
            "/api/v1/analytics/overview",
            params=params,
            headers=registered_user["headers"],
        )
        assert res.status_code == 422, params


@pytest.mark.asyncio
async def test_overview_scopes_to_one_subject(client, registered_user, subject):
    other = await client.post(
        "/api/v1/subjects", json={"title": "Statistics"}, headers=registered_user["headers"]
    )
    other_id = other.json()["id"]
    mine_quiz = await _seed_quiz(registered_user["user_id"], subject, "Physics")
    their_quiz = await _seed_quiz(registered_user["user_id"], other_id, "Regression")
    base = datetime(2024, 8, 1, 8, 0, 0)
    await _seed_attempt(
        registered_user["user_id"], subject, mine_quiz, score=2, completed_at=base
    )
    await _seed_attempt(
        registered_user["user_id"], other_id, their_quiz, score=0, completed_at=base + timedelta(hours=1)
    )
    await _seed_mastery(registered_user["user_id"], subject, "Momentum", 0.3)
    await _seed_mastery(registered_user["user_id"], other_id, "Bayes", 0.2)

    res = await client.get(
        "/api/v1/analytics/overview",
        params={"subject_id": subject},
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["totals"]["subject_count"] == 1
    assert body["totals"]["quiz_count"] == 1
    assert body["totals"]["attempt_count"] == 1
    assert [entry["quiz_title"] for entry in body["timeline"]] == ["Physics"]
    assert [s["subject_title"] for s in body["subjects"]] == ["Retrieval Strategies"]
    assert [t["topic"] for t in body["weakest_topics"]] == ["Momentum"]
    # The mastery average pools only the scoped subject's topics.
    assert body["totals"]["average_topic_mastery"] == 0.3


@pytest.mark.asyncio
async def test_overview_is_owner_scoped(client, registered_user, other_user, subject):
    quiz_id = await _seed_quiz(registered_user["user_id"], subject, "Private Quiz")
    await _seed_attempt(
        registered_user["user_id"],
        subject,
        quiz_id,
        score=1,
        completed_at=datetime(2024, 9, 1, 8, 0, 0),
    )

    intruder = await client.get(
        "/api/v1/analytics/overview", headers=other_user["headers"]
    )
    assert intruder.status_code == 200
    body = intruder.json()
    assert body["totals"]["attempt_count"] == 0
    assert body["totals"]["quiz_count"] == 0
    assert body["timeline"] == []

    foreign_subject = await client.get(
        "/api/v1/analytics/overview",
        params={"subject_id": subject},
        headers=other_user["headers"],
    )
    assert foreign_subject.status_code == 404

    anonymous = await client.get("/api/v1/analytics/overview")
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_overview_counts_survive_quiz_deletion(client, registered_user, subject):
    """A deleted quiz leaves the subject, but the work it produced is history."""

    keep = await _seed_quiz(registered_user["user_id"], subject, "Keep")
    drop = await _seed_quiz(registered_user["user_id"], subject, "Drop")
    base = datetime(2024, 10, 1, 8, 0, 0)
    await _seed_attempt(
        registered_user["user_id"], subject, keep, score=1, completed_at=base
    )
    await _seed_attempt(
        registered_user["user_id"], subject, drop, score=2, completed_at=base + timedelta(hours=1)
    )

    deleted = await client.delete(
        f"/api/v1/quizzes/{drop}", headers=registered_user["headers"]
    )
    assert deleted.status_code == 204

    res = await client.get(
        "/api/v1/analytics/overview", headers=registered_user["headers"]
    )
    body = res.json()
    assert body["totals"]["quiz_count"] == 1
    assert body["totals"]["attempt_count"] == 2
    # The deleted quiz's attempt is still on the timeline.
    assert [entry["quiz_title"] for entry in body["timeline"]] == ["Drop", "Keep"]


@pytest.mark.asyncio
async def test_performance_returns_a_bounded_attempt_list_with_full_totals(
    client, registered_user, subject
):
    """`/performance` keeps its totals exact while capping the rows it returns."""

    quiz_id = await _seed_quiz(registered_user["user_id"], subject, "Perf")
    base = datetime(2024, 11, 1, 8, 0, 0)
    for i in range(60):
        await _seed_attempt(
            registered_user["user_id"],
            subject,
            quiz_id,
            score=i % 2,
            completed_at=base + timedelta(minutes=i),
        )

    res = await client.get(
        f"/api/v1/analytics/subjects/{subject}/performance",
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    body = res.json()

    assert body["summary"]["total_attempts"] == 60
    assert body["summary"]["total_questions"] == 120
    assert body["summary"]["best_percentage"] == 50.0
    assert body["summary"]["average_percentage"] == 25.0
    # The list is capped, and it is the most recent slice that survives.
    assert len(body["attempts"]) == 50
    assert body["attempts"][0]["completed_at"] >= body["attempts"][-1]["completed_at"]


@pytest.mark.asyncio
async def test_performance_totals_match_the_existing_breakdown(
    client, registered_user, subject
):
    """The aggregate summary still agrees with the per-difficulty breakdown."""

    quiz_id = await _seed_quiz(registered_user["user_id"], subject, "Breakdown")
    base = datetime(2024, 12, 1, 8, 0, 0)
    for i in range(4):
        await _seed_attempt(
            registered_user["user_id"],
            subject,
            quiz_id,
            score=1 if i % 2 == 0 else 0,
            completed_at=base + timedelta(hours=i),
        )

    res = await client.get(
        f"/api/v1/analytics/subjects/{subject}/performance",
        headers=registered_user["headers"],
    )
    summary = res.json()["summary"]
    assert summary["total_attempts"] == 4
    assert summary["total_questions"] == 8
    assert summary["total_correct"] == 2
    assert summary["total_incorrect"] == 6
    assert summary["accuracy"] == 25.0
    assert summary["best_percentage"] == 50.0


@pytest.mark.asyncio
async def test_overview_counts_study_materials_and_due_flashcards(
    client, registered_user, subject, seeded_chunks
):
    """The material headline figures come from the same response as the rest."""

    await seeded_chunks(["Ready document one.", "Ready document two."], title="Ready.pdf")
    await seeded_chunks(
        ["Still processing."], title="Processing.pdf", status="PROCESSING"
    )
    await _seed_due_flashcards(registered_user["user_id"], subject, count=3)

    res = await client.get(
        "/api/v1/analytics/overview", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    totals = res.json()["totals"]

    # Two documents (one still processing); the processing one keeps its chunk,
    # so two of the three chunks belong to the READY document.
    assert totals["document_count"] == 2
    assert totals["ready_document_count"] == 1
    assert totals["chunk_count"] == 3
    # Never-studied cards are all due.
    assert totals["due_flashcard_count"] == 3


@pytest.mark.asyncio
async def test_overview_computes_the_average_topic_mastery(
    client, registered_user, subject
):
    """The headline mastery stat is the mean over the user's own topic rows."""

    await _seed_mastery(registered_user["user_id"], subject, "Circulation", 45.0)
    await _seed_mastery(registered_user["user_id"], subject, "Nervous System", 85.0)

    res = await client.get(
        "/api/v1/analytics/overview", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    totals = res.json()["totals"]

    # (45.0 + 85.0) / 2, on the same 0-100 scale the engine stores.
    assert totals["average_topic_mastery"] == 65.0


@pytest.mark.asyncio
async def test_overview_material_totals_scope_to_one_subject(
    client, registered_user, subject, seeded_chunks
):
    """Passing ``subject_id`` narrows the material totals exactly like the rest."""

    other = await client.post(
        "/api/v1/subjects", json={"title": "Other"}, headers=registered_user["headers"]
    )
    other_id = other.json()["id"]
    await seeded_chunks(["Only in this subject."], title="Scoped.pdf")
    await _seed_due_flashcards(registered_user["user_id"], subject, count=2)
    await _seed_due_flashcards(registered_user["user_id"], other_id, count=4)

    res = await client.get(
        "/api/v1/analytics/overview",
        params={"subject_id": subject},
        headers=registered_user["headers"],
    )
    totals = res.json()["totals"]
    assert totals["document_count"] == 1
    assert totals["ready_document_count"] == 1
    assert totals["chunk_count"] == 1
    assert totals["due_flashcard_count"] == 2


@pytest.mark.asyncio
async def test_overview_material_totals_exclude_soft_deleted_subjects(
    client, registered_user, subject, seeded_chunks
):
    """Material in a soft-deleted subject no longer counts toward the dashboard."""

    doomed = await client.post(
        "/api/v1/subjects", json={"title": "Doomed"}, headers=registered_user["headers"]
    )
    doomed_id = doomed.json()["id"]
    async with TestAsyncSessionLocal() as session:
        doc = Document(
            subject_id=uuid.UUID(doomed_id),
            user_id=uuid.UUID(registered_user["user_id"]),
            title="Doomed.pdf",
            file_path="",
            file_size_bytes=10,
            status="READY",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        session.add(
            DocumentChunk(
                document_id=doc.id,
                subject_id=uuid.UUID(doomed_id),
                user_id=uuid.UUID(registered_user["user_id"]),
                chunk_index=1,
                content="Doomed chunk.",
                embedding=await embedding_service.get_embedding("Doomed chunk."),
            )
        )
        await session.commit()

    deleted = await client.delete(
        f"/api/v1/subjects/{doomed_id}", headers=registered_user["headers"]
    )
    assert deleted.status_code == 204

    await seeded_chunks(["Kept chunk."], title="Kept.pdf")
    res = await client.get(
        "/api/v1/analytics/overview", headers=registered_user["headers"]
    )
    totals = res.json()["totals"]
    assert totals["document_count"] == 1
    assert totals["ready_document_count"] == 1
    assert totals["chunk_count"] == 1


@pytest.mark.asyncio
async def test_overview_material_totals_are_owner_scoped(
    client, registered_user, other_user, subject, seeded_chunks
):
    """Another student's material never leaks into the totals."""

    await seeded_chunks(["Private chunk."], title="Private.pdf")
    await _seed_due_flashcards(registered_user["user_id"], subject, count=3)

    res = await client.get(
        "/api/v1/analytics/overview", headers=other_user["headers"]
    )
    totals = res.json()["totals"]
    assert totals["document_count"] == 0
    assert totals["chunk_count"] == 0
    assert totals["due_flashcard_count"] == 0
