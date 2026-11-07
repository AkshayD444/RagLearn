"""HTTP and persistence coverage for the Phase 8 flashcard subsystem.

The grounding contract is asserted end to end: every generated card must carry a
citation that resolves to a chunk the user actually owns.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.flashcard import Flashcard, FlashcardDeck, FlashcardReview
from tests.conftest import TestAsyncSessionLocal

MATERIAL = [
    "Vector databases store embeddings for semantic search.",
    "Retrieval augmented generation grounds answers in source documents.",
    "A chunk is a contiguous span of text extracted from one document page.",
]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _generate(ac, token: str, subject_id: str, **overrides):
    payload = {"subject_id": subject_id, "card_count": 3}
    payload.update(overrides)
    return await ac.post("/api/v1/flashcards/generate", json=payload, headers=_auth(token))


async def _seeded_deck(ac, registered_user: dict, seeded_chunks, **overrides) -> dict:
    """Creates a subject with indexed material and generates one deck from it."""
    await seeded_chunks(MATERIAL)
    res = await _generate(
        ac, registered_user["token"], registered_user["subject_id"], **overrides
    )
    assert res.status_code == 201, res.text
    return res.json()


# ``registered_user`` does not create a subject, so this fixture pairs the two.
@pytest.fixture
async def subject_owner(registered_user, subject):
    registered_user["subject_id"] = subject
    return registered_user


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flashcard_routes_require_authentication(anonymous_client, subject):
    # B2: `anonymous_client`, because the shared client is signed in by cookie
    # from the moment it registers; asserting 401 on it would prove nothing.
    client = anonymous_client
    deck_id = str(uuid.uuid4())

    assert (await client.post(
        "/api/v1/flashcards/generate", json={"subject_id": subject, "card_count": 3}
    )).status_code == 401
    assert (await client.get("/api/v1/flashcards/decks")).status_code == 401
    assert (await client.get(
        f"/api/v1/flashcards/decks/{deck_id}"
    )).status_code == 401
    assert (await client.delete(
        f"/api/v1/flashcards/decks/{deck_id}"
    )).status_code == 401
    assert (await client.post(
        f"/api/v1/flashcards/decks/{deck_id}/review",
        json={"reviews": [{"card_id": str(uuid.uuid4()), "is_correct": True}]},
    )).status_code == 401
    assert (await client.get(
        f"/api/v1/flashcards/subjects/{subject}/progress"
    )).status_code == 401


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_a_grounded_deck(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks)

    assert deck["subject_id"] == subject_owner["subject_id"]
    assert deck["card_count"] == 3
    assert len(deck["cards"]) == 3
    assert [card["position"] for card in deck["cards"]] == [0, 1, 2]

    for card in deck["cards"]:
        assert card["front_text"]
        assert card["back_text"]
        # Grounding: the card points at a real chunk of the user's own material.
        assert card["source_chunk_id"] is not None
        assert card["source_document_id"] is not None
        assert card["source_document_title"] == "Seed Notes.pdf"
        assert card["source_snippet"] in MATERIAL
        # Freshly generated cards have no review history yet.
        assert card["review_count"] == 0
        assert card["correct_count"] == 0


@pytest.mark.asyncio
async def test_generated_cards_only_use_seeded_chunk_ids(
    client, subject_owner, seeded_chunks
):
    """The citation must resolve to a chunk this user owns, not a fabricated id."""
    _, chunk_ids = await seeded_chunks(MATERIAL)
    deck = await _seeded_deck(client, subject_owner, seeded_chunks)

    seeded = set(chunk_ids)
    for card in deck["cards"]:
        assert card["source_chunk_id"] in seeded


@pytest.mark.asyncio
async def test_generate_honours_the_card_count(client, subject_owner, seeded_chunks):
    small = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    assert small["card_count"] == 1
    assert len(small["cards"]) == 1


@pytest.mark.asyncio
async def test_generate_defaults_to_ten_cards(client, subject_owner, seeded_chunks):
    await seeded_chunks(MATERIAL)
    res = await _generate(client, subject_owner["token"], subject_owner["subject_id"])
    assert res.status_code == 201
    # The offline generator can only produce one card per usable term, so the
    # default is an upper bound rather than a guarantee.
    assert 1 <= res.json()["card_count"] <= 10


@pytest.mark.asyncio
async def test_generate_rejects_out_of_range_card_counts(
    client, subject_owner, seeded_chunks
):
    await seeded_chunks(MATERIAL)
    token, subject_id = subject_owner["token"], subject_owner["subject_id"]

    too_small = await _generate(client, token, subject_id, card_count=0)
    assert too_small.status_code == 422

    too_large = await _generate(client, token, subject_id, card_count=41)
    assert too_large.status_code == 422


@pytest.mark.asyncio
async def test_generate_requires_a_valid_subject_id(client, registered_user):
    res = await _generate(client, registered_user["token"], "not-a-uuid")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_generate_without_study_material_is_a_400(client, subject_owner):
    res = await _generate(client, subject_owner["token"], subject_owner["subject_id"])
    assert res.status_code == 400
    assert "no indexed study materials" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_generate_is_scoped_to_the_caller(client, other_user, subject, seeded_chunks):
    await seeded_chunks(MATERIAL)
    res = await _generate(client, other_user["token"], subject)
    assert res.status_code == 404
    assert "access denied" in res.json()["detail"]


@pytest.mark.asyncio
async def test_generate_for_a_soft_deleted_subject_is_a_404(
    client, subject_owner, seeded_chunks
):
    await seeded_chunks(MATERIAL)
    assert (await client.delete(
        f"/api/v1/subjects/{subject_owner['subject_id']}",
        headers=subject_owner["headers"],
    )).status_code == 204

    res = await _generate(client, subject_owner["token"], subject_owner["subject_id"])
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_generate_normalises_a_blank_topic(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, topic="   ")
    assert deck["topic"] is None
    assert all(card["topic"] is None for card in deck["cards"])


@pytest.mark.asyncio
async def test_generate_stores_the_requested_topic(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, topic="Embeddings")
    assert deck["topic"] == "Embeddings"
    assert all(card["topic"] == "Embeddings" for card in deck["cards"])


# ---------------------------------------------------------------------------
# Listing and retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_decks_is_empty_for_a_new_subject(client, subject_owner):
    res = await client.get("/api/v1/flashcards/decks", headers=subject_owner["headers"])
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_list_decks_reports_review_progress(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=2)

    listed = await client.get(
        "/api/v1/flashcards/decks", headers=subject_owner["headers"]
    )
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["id"] == deck["id"]
    assert body[0]["card_count"] == 2
    assert body[0]["reviewed_card_count"] == 0
    assert body[0]["correct_count"] == 0

    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": index == 0}
                for index, card in enumerate(deck["cards"])
            ]
        },
        headers=subject_owner["headers"],
    )

    after = await client.get(
        "/api/v1/flashcards/decks", headers=subject_owner["headers"]
    )
    assert after.json()[0]["reviewed_card_count"] == 2
    assert after.json()[0]["correct_count"] == 1


@pytest.mark.asyncio
async def test_list_decks_can_be_filtered_by_subject(
    client, subject_owner, seeded_chunks
):
    await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)

    matched = await client.get(
        f"/api/v1/flashcards/decks?subject_id={subject_owner['subject_id']}",
        headers=subject_owner["headers"],
    )
    assert len(matched.json()) == 1

    empty = await client.get(
        f"/api/v1/flashcards/decks?subject_id={uuid.uuid4()}",
        headers=subject_owner["headers"],
    )
    assert empty.status_code == 404


@pytest.mark.asyncio
async def test_list_decks_rejects_a_foreign_subject(
    client, subject_owner, other_user, seeded_chunks
):
    await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.get(
        f"/api/v1/flashcards/decks?subject_id={subject_owner['subject_id']}",
        headers=other_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_decks_never_leaks_another_users_decks(
    client, subject_owner, other_user, seeded_chunks
):
    await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.get("/api/v1/flashcards/decks", headers=other_user["headers"])
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_get_deck_returns_cards_and_progress(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=2)

    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=subject_owner["headers"]
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == deck["id"]
    assert len(body["cards"]) == 2
    assert body["cards"][0]["front_text"] == deck["cards"][0]["front_text"]


@pytest.mark.asyncio
async def test_get_deck_is_scoped_to_the_caller(client, subject_owner, other_user, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=other_user["headers"]
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_deck_with_a_malformed_id_is_a_422(client, subject_owner):
    res = await client.get(
        "/api/v1/flashcards/decks/not-a-uuid", headers=subject_owner["headers"]
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_deck_hides_it_but_keeps_reviews(
    client, subject_owner, seeded_chunks
):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=2)
    headers = subject_owner["headers"]

    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": True} for card in deck["cards"]
            ]
        },
        headers=headers,
    )

    deleted = await client.delete(f"/api/v1/flashcards/decks/{deck['id']}", headers=headers)
    assert deleted.status_code == 204

    assert (await client.get("/api/v1/flashcards/decks", headers=headers)).json() == []
    assert (await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=headers
    )).status_code == 404

    # The review log survives the soft delete.
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(FlashcardReview).where(FlashcardReview.deck_id == uuid.UUID(deck["id"]))
        )
        assert len(list(result.scalars().all())) == 2


@pytest.mark.asyncio
async def test_deleting_a_deck_twice_is_a_404(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    headers = subject_owner["headers"]

    assert (await client.delete(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=headers
    )).status_code == 204
    assert (await client.delete(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=headers
    )).status_code == 404


@pytest.mark.asyncio
async def test_delete_deck_is_scoped_to_the_caller(
    client, subject_owner, other_user, seeded_chunks
):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.delete(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=other_user["headers"]
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Reviewing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_records_a_session_and_returns_a_summary(
    client, subject_owner, seeded_chunks
):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=3)
    headers = subject_owner["headers"]

    payload = {
        "reviews": [
            {"card_id": card["id"], "is_correct": index < 2, "response": f"try {index}"}
            for index, card in enumerate(deck["cards"])
        ]
    }
    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review", json=payload, headers=headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deck_id"] == deck["id"]
    assert body["title"] == deck["title"]
    assert body["reviewed_count"] == 3
    assert body["correct_count"] == 2
    assert body["incorrect_count"] == 1
    assert body["percentage"] == 66.7
    assert len(body["results"]) == 3
    for outcome in body["results"]:
        assert outcome["review_count"] == 1
        assert outcome["correct_count"] == (1 if outcome["is_correct"] else 0)
        assert outcome["response"].startswith("try ")


@pytest.mark.asyncio
async def test_review_persists_one_row_per_card(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=2)
    headers = subject_owner["headers"]

    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": True} for card in deck["cards"]
            ]
        },
        headers=headers,
    )

    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(FlashcardReview).where(FlashcardReview.deck_id == uuid.UUID(deck["id"]))
        )
        rows = list(result.scalars().all())
        assert len(rows) == 2
        for row in rows:
            assert row.user_id == uuid.UUID(subject_owner["user_id"])
            assert row.subject_id == uuid.UUID(subject_owner["subject_id"])
            assert row.is_correct is True
            assert row.reviewed_at is not None


@pytest.mark.asyncio
async def test_review_appends_rather_than_replaces(client, subject_owner, seeded_chunks):
    """Reviews are an append-only log, so a second pass accumulates history."""
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    headers = subject_owner["headers"]
    card_id = deck["cards"][0]["id"]

    def _payload(is_correct: bool) -> dict:
        return {"reviews": [{"card_id": card_id, "is_correct": is_correct}]}

    first = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json=_payload(True),
        headers=headers,
    )
    assert first.json()["results"][0]["review_count"] == 1
    assert first.json()["results"][0]["correct_count"] == 1

    second = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json=_payload(False),
        headers=headers,
    )
    assert second.json()["results"][0]["review_count"] == 2
    # Correct count is cumulative history, not the latest answer.
    assert second.json()["results"][0]["correct_count"] == 1

    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(FlashcardReview).where(FlashcardReview.card_id == uuid.UUID(card_id))
        )
        assert len(list(result.scalars().all())) == 2


@pytest.mark.asyncio
async def test_review_rejects_duplicate_cards(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=2)
    card_id = deck["cards"][0]["id"]

    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card_id, "is_correct": True},
                {"card_id": card_id, "is_correct": False},
            ]
        },
        headers=subject_owner["headers"],
    )
    assert res.status_code == 400
    assert "duplicate" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_review_rejects_partial_coverage(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=3)

    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": [{"card_id": deck["cards"][0]["id"], "is_correct": True}]},
        headers=subject_owner["headers"],
    )
    assert res.status_code == 400
    assert "exactly one result for every card" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_review_rejects_a_card_from_another_deck(
    client, subject_owner, seeded_chunks
):
    first = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    second = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)

    res = await client.post(
        f"/api/v1/flashcards/decks/{first['id']}/review",
        json={"reviews": [{"card_id": second["cards"][0]["id"], "is_correct": True}]},
        headers=subject_owner["headers"],
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_review_rejects_an_empty_review_list(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": []},
        headers=subject_owner["headers"],
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_review_is_scoped_to_the_caller(client, subject_owner, other_user, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": True} for card in deck["cards"]
            ]
        },
        headers=other_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_review_of_a_soft_deleted_deck_is_a_404(
    client, subject_owner, seeded_chunks
):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    headers = subject_owner["headers"]
    await client.delete(f"/api/v1/flashcards/decks/{deck['id']}", headers=headers)

    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": True} for card in deck["cards"]
            ]
        },
        headers=headers,
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_is_empty_without_decks(client, subject_owner):
    res = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=subject_owner["headers"],
    )
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_progress_aggregates_cards_and_reviews_by_topic(
    client, subject_owner, seeded_chunks
):
    deck = await _seeded_deck(
        client, subject_owner, seeded_chunks, card_count=2, topic="Embeddings"
    )
    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": index == 0}
                for index, card in enumerate(deck["cards"])
            ]
        },
        headers=subject_owner["headers"],
    )

    res = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=subject_owner["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["topic"] == "Embeddings"
    assert entry["subject_id"] == subject_owner["subject_id"]
    assert entry["card_count"] == 2
    assert entry["reviewed_count"] == 2
    assert entry["correct_count"] == 1
    assert entry["accuracy"] == 50.0


@pytest.mark.asyncio
async def test_progress_labels_untagged_cards_as_general(
    client, subject_owner, seeded_chunks
):
    await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=subject_owner["headers"],
    )
    assert res.json()[0]["topic"] == "General"


@pytest.mark.asyncio
async def test_progress_annotates_mastery_without_writing_to_it(
    client, subject_owner, seeded_chunks
):
    """Topic mastery is read-only here: the Phase 6/7 evidence model is untouched."""
    from app.models.mastery import TopicMastery

    deck = await _seeded_deck(
        client, subject_owner, seeded_chunks, card_count=1, topic="Embeddings"
    )

    async with TestAsyncSessionLocal() as session:
        session.add(
            TopicMastery(
                user_id=uuid.UUID(subject_owner["user_id"]),
                subject_id=uuid.UUID(subject_owner["subject_id"]),
                topic="Embeddings",
                questions_attempted=10,
                questions_correct=7,
                questions_incorrect=3,
                accuracy=70.0,
                mastery_score=70.0,
                mastery_level="proficient",
            )
        )
        await session.commit()

    before = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=subject_owner["headers"],
    )
    assert before.json()[0]["mastery_level"] == "proficient"
    assert before.json()[0]["mastery_score"] == 70.0

    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": card["id"], "is_correct": False} for card in deck["cards"]
            ]
        },
        headers=subject_owner["headers"],
    )

    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(TopicMastery).where(
                TopicMastery.user_id == uuid.UUID(subject_owner["user_id"]),
                TopicMastery.topic == "Embeddings",
            )
        )
        mastery = result.scalar_one()
        # Flashcard review evidence must not leak into the quiz evidence model.
        assert mastery.questions_attempted == 10
        assert mastery.questions_correct == 7
        assert mastery.questions_incorrect == 3

    after = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=subject_owner["headers"],
    )
    assert after.json()[0]["mastery_level"] == "proficient"
    assert after.json()[0]["correct_count"] == 0


@pytest.mark.asyncio
async def test_progress_excludes_soft_deleted_decks(client, subject_owner, seeded_chunks):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    await client.delete(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=subject_owner["headers"]
    )

    res = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=subject_owner["headers"],
    )
    assert res.json() == []


@pytest.mark.asyncio
async def test_progress_is_scoped_to_the_caller(
    client, subject_owner, other_user, seeded_chunks
):
    await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    res = await client.get(
        f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress",
        headers=other_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_progress_enforces_limit_bounds(client, subject_owner, seeded_chunks):
    await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    base = f"/api/v1/flashcards/subjects/{subject_owner['subject_id']}/progress"
    headers = subject_owner["headers"]

    assert (await client.get(f"{base}?limit=0", headers=headers)).status_code == 422
    assert (await client.get(f"{base}?limit=101", headers=headers)).status_code == 422
    assert (await client.get(f"{base}?limit=1", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_progress_rejects_a_malformed_subject_id(client, subject_owner):
    res = await client.get(
        "/api/v1/flashcards/subjects/not-a-uuid/progress",
        headers=subject_owner["headers"],
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Cascade behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_document_keeps_card_citations_readable(
    client, subject_owner, seeded_chunks
):
    """Citation text is a snapshot, so a card outlives its source document.

    Whether the ``document_id``/``chunk_id`` foreign keys are actually nulled is
    PostgreSQL enforcement and is covered by
    ``tests/integration/test_flashcard_citations.py``; SQLite does not enable
    ``PRAGMA foreign_keys``, so only the snapshot guarantee is asserted here.
    """
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=1)
    card = deck["cards"][0]
    document_id = card["source_document_id"]

    deleted = await client.delete(
        f"/api/v1/documents/{document_id}", headers=subject_owner["headers"]
    )
    assert deleted.status_code == 204

    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=subject_owner["headers"]
    )
    assert res.status_code == 200
    stored = res.json()["cards"][0]
    # The text snapshot is what keeps the card meaningful after deletion.
    assert stored["source_document_title"] == "Seed Notes.pdf"
    assert stored["source_snippet"] == card["source_snippet"]
    assert stored["front_text"] == card["front_text"]


@pytest.mark.asyncio
async def test_deck_card_count_matches_the_stored_cards(
    client, subject_owner, seeded_chunks
):
    deck = await _seeded_deck(client, subject_owner, seeded_chunks, card_count=2)

    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(FlashcardDeck)
            .where(FlashcardDeck.id == uuid.UUID(deck["id"]))
            .options(selectinload(FlashcardDeck.cards))
        )
        stored = result.scalar_one()
        cards = list(stored.cards)
        assert stored.card_count == len(cards) == 2
        assert [card.position for card in cards] == [0, 1]
