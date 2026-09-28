"""Tests for Phase 10 adaptive learning intelligence.

Three layers are pinned down:

* the mastery estimator (:mod:`app.services.adaptive_mastery`) is pure, so its
  difficulty and recency weighting, the flashcard blend, and the confidence gate
  are all exercised without a database;
* the extended priority and the recent-performance weak-topic rule are
  deterministic pure functions that must not regress the earlier guarantees
  (a single failed question stays below the review cutoff, a uniformly weak
  topic does not redundantly fire a "decline" reason);
* the two ``/next`` endpoints are covered end to end for selection, activity
  routing, insufficient data, ownership, and determinism.
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Tuple

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.attempt import QuizAttempt, QuizAttemptAnswer
from app.models.flashcard import Flashcard, FlashcardDeck
from app.models.mastery import TopicMastery
from app.models.quiz import Quiz, QuizQuestion
from app.rag.embeddings import embedding_service
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.adaptive_learning import TopicMetrics, extended_priority_score
from app.services.adaptive_mastery import ScoredAnswer, compute_adaptive_mastery
from app.services.student_intelligence import (
    compute_accuracy,
    compute_mastery,
    mastery_level,
)
from app.services.weak_topics import (
    LOW_ACCURACY,
    RECENT_POOR_PERFORMANCE,
    REPEATED_MISTAKES,
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


async def _create_subject(ac: AsyncClient, token: str, title: str = "Next Subject"):
    res = await ac.post("/api/v1/subjects", json={"title": title}, headers=_auth(token))
    assert res.status_code == 201
    return res.json()["id"]


async def _next(ac: AsyncClient, token: str, subject_id: str | None = None):
    url = "/api/v1/recommendations/next"
    if subject_id is not None:
        url = f"/api/v1/recommendations/subjects/{subject_id}/next"
    return await ac.get(url, headers=_auth(token))


# ---------------------------------------------------------------------------
# Mastery estimator: pure and deterministic
# ---------------------------------------------------------------------------


def _scored(outcomes: List[Tuple[bool, str, datetime]]) -> List[ScoredAnswer]:
    return [ScoredAnswer(difficulty=diff, is_correct=ok, answered_at=at) for ok, diff, at in outcomes]


def test_correct_hard_answers_count_more_than_correct_easy_ones():
    now = datetime.utcnow()
    hard_leaning = _scored(
        [(True, "hard", now), (False, "easy", now), (True, "medium", now)]
    )
    easy_leaning = _scored(
        [(True, "easy", now), (False, "hard", now), (True, "medium", now)]
    )

    assert compute_adaptive_mastery(answers=hard_leaning, now=now) is not None
    assert (
        compute_adaptive_mastery(answers=hard_leaning, now=now).weighted_accuracy
        > compute_adaptive_mastery(answers=easy_leaning, now=now).weighted_accuracy
    )


def test_recent_answers_carry_more_weight_than_stale_ones():
    now = datetime.utcnow()
    old = now - timedelta(days=30)
    improving = _scored([(False, "medium", old), (True, "medium", now)])
    slipping = _scored([(True, "medium", old), (False, "medium", now)])

    improving_est = compute_adaptive_mastery(answers=improving, now=now)
    slipping_est = compute_adaptive_mastery(answers=slipping, now=now)
    assert improving_est.weighted_accuracy > slipping_est.weighted_accuracy
    # Same raw correct/incorrect split, so recency is the only difference.
    assert improving_est.raw_accuracy == slipping_est.raw_accuracy


def test_flashcard_channel_blends_into_the_estimate_without_dominating():
    now = datetime.utcnow()
    # An imperfect but solid quiz history (8/10, 80%) and a flawless card set.
    answers = _scored([(True, "medium", now)] * 8 + [(False, "medium", now)] * 2)

    without_cards = compute_adaptive_mastery(answers=answers, now=now)
    with_cards = compute_adaptive_mastery(
        answers=answers, flashcard_reviews=10, flashcard_correct=10, now=now
    )

    assert without_cards.mastery == 80.0
    assert with_cards.mastery == 85.0
    assert with_cards.mastery > without_cards.mastery


def test_no_evidence_returns_none():
    assert compute_adaptive_mastery(answers=[], now=datetime.utcnow()) is None
    assert (
        compute_adaptive_mastery(
            answers=[], flashcard_reviews=0, flashcard_correct=0, now=datetime.utcnow()
        )
        is None
    )


def test_absent_flashcard_channel_does_not_penalize_a_flawless_history():
    now = datetime.utcnow()
    estimate = compute_adaptive_mastery(
        answers=_scored([(True, "medium", now)] * 10), now=now
    )
    assert estimate.mastery == 100.0


def test_flashcard_only_evidence_still_produces_an_estimate():
    now = datetime.utcnow()
    estimate = compute_adaptive_mastery(
        answers=[], flashcard_reviews=10, flashcard_correct=9, now=now
    )
    assert estimate.questions_attempted == 0
    assert estimate.flashcard_reviews == 10
    assert estimate.mastery == 90.0


def test_thin_history_is_confidence_gated():
    now = datetime.utcnow()
    estimate = compute_adaptive_mastery(
        answers=_scored([(True, "medium", now)]), now=now
    )
    assert estimate.confidence == 0.1
    assert estimate.mastery == 10.0
    assert estimate.level == "novice"


def test_difficulty_factor_defaults_to_medium():
    now = datetime.utcnow()
    hard = compute_adaptive_mastery(
        answers=_scored([(True, "hard", now), (True, "expert", now)]), now=now
    )
    assert hard.difficulty_factor == 1.6
    unknown = compute_adaptive_mastery(
        answers=_scored([(True, "???", now)]), now=now
    )
    assert unknown.difficulty_factor == 1.0


def test_repeated_attempts_accumulate_evidence():
    now = datetime.utcnow()
    single_attempt = compute_adaptive_mastery(
        answers=_scored([(True, "medium", now)] * 1), now=now
    )
    repeated_attempts = compute_adaptive_mastery(
        answers=_scored([(True, "medium", now)] * 5), now=now
    )
    assert repeated_attempts.confidence > single_attempt.confidence
    assert repeated_attempts.mastery > single_attempt.mastery


# ---------------------------------------------------------------------------
# Extended priority: deterministic and difficulty/mastery aware
# ---------------------------------------------------------------------------


def _metrics(
    topic: str,
    *,
    attempted: int,
    correct: int,
    last_attempt_at: datetime,
    attempts: int = 2,
) -> TopicMetrics:
    return TopicMetrics(
        subject_id=uuid.uuid4(),
        subject_title="Unit Subject",
        topic=topic,
        attempts=attempts,
        questions_attempted=attempted,
        questions_correct=correct,
        questions_incorrect=attempted - correct,
        accuracy=compute_accuracy(correct, attempted),
        mastery_score=compute_mastery(correct, attempted),
        mastery_level=mastery_level(compute_mastery(correct, attempted)),
        last_attempt_at=last_attempt_at,
    )


def test_extended_priority_is_deterministic():
    now = datetime.utcnow()
    item = _metrics("Deterministic", attempted=5, correct=1, last_attempt_at=now)
    assert extended_priority_score(item) == extended_priority_score(item)


def test_harder_difficulty_raises_extended_priority():
    now = datetime.utcnow()
    item = _metrics("Diff", attempted=5, correct=2, last_attempt_at=now)
    assert (
        extended_priority_score(item, difficulty_index=1.0)
        < extended_priority_score(item, difficulty_index=1.8)
    )


def test_weaker_mastery_raises_extended_priority():
    now = datetime.utcnow()
    item = _metrics("Mastery", attempted=5, correct=2, last_attempt_at=now)
    assert (
        extended_priority_score(item, mastery_ratio=0.0)
        > extended_priority_score(item, mastery_ratio=1.0)
    )


def test_single_failed_question_stays_below_the_review_cutoff():
    now = datetime.utcnow()
    item = _metrics("OneAttempt", attempted=1, correct=0, last_attempt_at=now, attempts=1)
    assert extended_priority_score(item) < 40.0


# ---------------------------------------------------------------------------
# Recent-performance weak-topic rule
# ---------------------------------------------------------------------------


def _mastery_row(topic: str, *, attempted: int, correct: int) -> TopicMastery:
    incorrect = attempted - correct
    score = compute_mastery(correct, attempted)
    return TopicMastery(
        user_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        topic=topic,
        attempts=2,
        questions_attempted=attempted,
        questions_correct=correct,
        questions_incorrect=incorrect,
        accuracy=compute_accuracy(correct, attempted),
        mastery_score=score,
        mastery_level=mastery_level(score),
        last_attempt_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def test_recent_decline_fires_the_recent_performance_rule():
    reasons = classify_topic(
        attempts=2,
        questions_attempted=10,
        questions_incorrect=3,
        accuracy=60.0,
        recent_attempted=3,
        recent_correct=0,
    )
    codes = [reason.code for reason in reasons]
    assert RECENT_POOR_PERFORMANCE in codes
    # 3 mistakes still trips repeated_mistakes here; accuracy 60 is not low.
    assert codes == [REPEATED_MISTAKES, RECENT_POOR_PERFORMANCE]


def test_uniformly_weak_topic_does_not_fire_the_decline_rule():
    reasons = classify_topic(
        attempts=2,
        questions_attempted=10,
        questions_incorrect=10,
        accuracy=0.0,
        recent_attempted=5,
        recent_correct=0,
    )
    codes = [reason.code for reason in reasons]
    assert RECENT_POOR_PERFORMANCE not in codes
    assert codes == [REPEATED_MISTAKES, LOW_ACCURACY]


def test_recovering_topic_never_fires_the_decline_rule():
    reasons = classify_topic(
        attempts=2,
        questions_attempted=10,
        questions_incorrect=5,
        accuracy=50.0,
        recent_attempted=5,
        recent_correct=5,
    )
    assert RECENT_POOR_PERFORMANCE not in [reason.code for reason in reasons]


def test_single_recent_answer_is_not_enough_for_a_decline():
    reasons = classify_topic(
        attempts=2,
        questions_attempted=10,
        questions_incorrect=4,
        accuracy=60.0,
        recent_attempted=1,
        recent_correct=0,
    )
    assert RECENT_POOR_PERFORMANCE not in [reason.code for reason in reasons]


def test_weak_topic_confidence_labels_the_flag():
    row = _mastery_row("Declining", attempted=5, correct=4)
    decline_only = detect_weak_topics(
        [row], recent_by_topic={row.topic: (3, 0)}
    )
    # accuracy 80, so low_accuracy stays silent; only the decline rule fires.
    assert [reason.code for reason in decline_only[0].reasons] == [RECENT_POOR_PERFORMANCE]
    assert decline_only[0].confidence == "low"

    weak_row = _mastery_row("AlwaysWeak", attempted=5, correct=1)
    flagged = detect_weak_topics([weak_row])
    assert flagged[0].confidence == "confirmed"


# ---------------------------------------------------------------------------
# GET /api/v1/recommendations/next
# ---------------------------------------------------------------------------


async def _seed_topic_history(
    user_id: str,
    subject_id: str,
    topic: str,
    outcomes: List[Tuple[bool, datetime]],
    difficulty: str = "medium",
) -> None:
    """Directly seeds a Quiz, an attempt, answers, and a mastery row."""
    correct = sum(1 for ok, _ in outcomes if ok)
    attempted = len(outcomes)
    completed_at = outcomes[-1][1] if outcomes else datetime.utcnow()

    async with TestAsyncSessionLocal() as session:
        quiz = Quiz(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title=f"{topic} Quiz",
            difficulty=difficulty,
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
                explanation="Seeded for next-learning tests.",
                difficulty=difficulty,
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
                    difficulty=difficulty,
                    topic=topic,
                    created_at=created_at,
                )
            )

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
                mastery_score=compute_mastery(correct, attempted),
                mastery_level=mastery_level(compute_mastery(correct, attempted)),
                last_attempt_at=completed_at,
            )
        )
        await session.commit()


async def _seed_chunks(user_id: str, subject_id: str, texts: list) -> None:
    async with TestAsyncSessionLocal() as session:
        doc = Document(
            subject_id=uuid.UUID(subject_id),
            user_id=uuid.UUID(user_id),
            title="Next Notes.pdf",
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


async def _seed_due_flashcards(user_id: str, subject_id: str, topic: str, count: int = 3) -> None:
    """Seeds a deck of never-studied cards, which are all due immediately."""
    async with TestAsyncSessionLocal() as session:
        deck = FlashcardDeck(
            user_id=uuid.UUID(user_id),
            subject_id=uuid.UUID(subject_id),
            title=f"{topic} Deck",
            topic=topic,
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
                    topic=topic,
                    position=i,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_next_learning_requires_authentication():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        assert (await ac.get("/api/v1/recommendations/next")).status_code == 401
        assert (
            await ac.get(f"/api/v1/recommendations/subjects/{uuid.uuid4()}/next")
        ).status_code == 401


@pytest.mark.asyncio
async def test_next_learning_insufficient_data_without_history():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_empty@example.com")
        subject_id = await _create_subject(ac, token)

        for target in (None, subject_id):
            res = await _next(ac, token, target)
            assert res.status_code == 200
            body = res.json()
            assert body["status"] == "insufficient_data"
            assert body["recommendation"] is None
            assert body["reason"]


@pytest.mark.asyncio
async def test_next_learning_chooses_the_weakest_topic_across_subjects():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_cross@example.com")
        weak_subject = await _create_subject(ac, token, "Weak Subject")
        medium_subject = await _create_subject(ac, token, "Medium Subject")

        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, weak_subject, "WeakTopic", [(False, now - timedelta(hours=i)) for i in range(5)]
        )
        # Decent overall (4/5) with a single recent wrong answer: not a weak topic.
        await _seed_topic_history(
            user_id,
            medium_subject,
            "DecentTopic",
            [(True, now - timedelta(hours=i + 1)) for i in range(4)]
            + [(False, now - timedelta(minutes=30))],
        )

        res = await _next(ac, token)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        rec = body["recommendation"]
        assert rec["subject_id"] == weak_subject
        assert rec["topic"] == "WeakTopic"
        assert rec["activity_type"] in {"study", "quiz", "flashcards"}
        assert rec["mastery_level"] in {"mastered", "proficient", "developing", "novice"}
        assert rec["priority"] > 0
        assert rec["questions_attempted"] == 5


@pytest.mark.asyncio
async def test_next_learning_recommends_quiz_for_a_weak_topic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_quiz@example.com")
        subject_id = await _create_subject(ac, token)
        await _seed_chunks(user_id, subject_id, ["Some indexed material for the top contenders."])
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, subject_id, "Algebra", [(False, now - timedelta(hours=i)) for i in range(5)]
        )

        body = (await _next(ac, token)).json()
        assert body["status"] == "ok"
        rec = body["recommendation"]
        assert rec["topic"] == "Algebra"
        assert rec["activity_type"] == "quiz"
        assert "Low accuracy" in rec["reason"]
        assert rec["accuracy"] == 0.0
        assert rec["weighted_accuracy"] == 0.0
        assert rec["suggested_difficulty"] == "easy"


@pytest.mark.asyncio
async def test_next_learning_recommends_study_when_no_material_exists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_study@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, subject_id, "Unmaterial", [(False, now - timedelta(hours=i)) for i in range(4)]
        )

        body = (await _next(ac, token)).json()
        assert body["status"] == "ok"
        rec = body["recommendation"]
        assert rec["topic"] == "Unmaterial"
        assert rec["activity_type"] == "study"
        assert "upload" in rec["reason"].lower()


@pytest.mark.asyncio
async def test_next_learning_recommends_flashcards_when_cards_are_due():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_flashcards@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, subject_id, "Biology", [(False, now - timedelta(hours=i)) for i in range(5)]
        )
        await _seed_due_flashcards(user_id, subject_id, "Biology", count=3)

        body = (await _next(ac, token)).json()
        assert body["status"] == "ok"
        rec = body["recommendation"]
        assert rec["topic"] == "Biology"
        assert rec["activity_type"] == "flashcards"
        assert rec["due_cards"] == 3
        assert "due" in rec["reason"].lower()


@pytest.mark.asyncio
async def test_next_learning_is_deterministic():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_deterministic@example.com")
        subject_id = await _create_subject(ac, token)
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, subject_id, "TopicA", [(False, now - timedelta(hours=i)) for i in range(5)]
        )
        await _seed_topic_history(
            user_id, subject_id, "TopicB", [(True, now - timedelta(hours=i)) for i in range(5)]
        )

        first = (await _next(ac, token)).json()
        second = (await _next(ac, token)).json()
        assert first == second
        assert first["status"] == "ok"
        assert first["recommendation"]["topic"] == "TopicA"


@pytest.mark.asyncio
async def test_next_learning_subject_scoped_is_owned_only():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, _ = await _register(ac, "next_owner@example.com")
        intruder_token, _ = await _register(ac, "next_intruder@example.com")
        subject_id = await _create_subject(ac, token, "Owned Subject")

        for bad in (subject_id, str(uuid.uuid4())):
            res = await _next(ac, intruder_token, bad)
            assert res.status_code == 404

        unauthenticated = await ac.get(
            f"/api/v1/recommendations/subjects/{subject_id}/next"
        )
        assert unauthenticated.status_code in (401, 403)


@pytest.mark.asyncio
async def test_next_learning_empty_within_an_empty_subject():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_empty_subject@example.com")
        subject_id = await _create_subject(ac, token)
        body = (await _next(ac, token, subject_id)).json()
        assert body["status"] == "insufficient_data"
        assert body["recommendation"] is None


@pytest.mark.asyncio
async def test_next_learning_excludes_deleted_subjects():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "next_deleted@example.com")
        doomed = await _create_subject(ac, token, "Doomed Subject")
        alive = await _create_subject(ac, token, "Alive Subject")
        now = datetime.utcnow()
        await _seed_topic_history(
            user_id, doomed, "DoomedTopic", [(False, now - timedelta(hours=i)) for i in range(5)]
        )
        await _seed_topic_history(
            user_id, alive, "AliveTopic", [(True, now - timedelta(hours=i)) for i in range(5)]
        )

        deleted = await ac.delete(f"/api/v1/subjects/{doomed}", headers=_auth(token))
        assert deleted.status_code == 204

        body = (await _next(ac, token)).json()
        assert body["status"] == "ok"
        assert body["recommendation"]["subject_id"] == alive
        assert body["recommendation"]["topic"] == "AliveTopic"


@pytest.mark.asyncio
async def test_next_learning_does_not_leak_other_users_history():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "next_leak_a@example.com")
        token_b, _ = await _register(ac, "next_leak_b@example.com")

        subject_a = await _create_subject(ac, token_a, "A Subject")
        now = datetime.utcnow()
        await _seed_topic_history(
            user_a, subject_a, "SecretTopic", [(False, now - timedelta(hours=i)) for i in range(5)]
        )

        await _create_subject(ac, token_b, "B Subject")
        body = (await _next(ac, token_b)).json()
        assert body["status"] == "insufficient_data"

        body_a = (await _next(ac, token_a)).json()
        assert body_a["status"] == "ok"
        assert body_a["recommendation"]["topic"] == "SecretTopic"