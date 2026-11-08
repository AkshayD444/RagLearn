"""PostgreSQL integration coverage for flashcard citation durability.

``flashcards.document_id`` and ``flashcards.chunk_id`` are declared
``ondelete="SET NULL"`` so a card keeps its provenance when the material it was
generated from is deleted. SQLite does not enable ``PRAGMA foreign_keys``, so
the default suite cannot observe that behaviour; these tests can.

Run them with the repository's Docker Compose database::

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
        python -m pytest -m postgres

Without ``TEST_DATABASE_URL`` (or when it is not a PostgreSQL URL) every test in
this module skips instantly.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.main import app
from app.models.document import Document
from app.models.flashcard import Flashcard, FlashcardDeck, FlashcardReview
from tests.conftest import auth_headers, seed_document_with_chunks

pytestmark = pytest.mark.postgres

MATERIAL = [
    "Vector databases store embeddings for semantic search.",
    "Retrieval augmented generation grounds answers in source documents.",
]


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


async def _real_user_with_deck(pg_engine, texts=MATERIAL) -> tuple[str, str, dict]:
    """Registers a real user, creates a subject, seeds chunks, and generates a deck.

    The user and subject come from the API so the caller owns genuine rows, while
    the document and its vectorized chunks are seeded directly because the upload
    endpoint needs a real PDF on disk.
    """
    email = f"fc-{uuid.uuid4().hex[:10]}@example.com"

    async with _client() as client:
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "full_name": "Flashcard Student",
                "password": "password123",
            },
        )
        assert registered.status_code == 201, registered.text
        token = registered.json()["access_token"]
        user_id = registered.json()["user"]["id"]
        headers = auth_headers(token)

        subject_res = await client.post(
            "/api/v1/subjects", json={"title": "Vector Notes"}, headers=headers
        )
        assert subject_res.status_code == 201, subject_res.text
        subject_id = subject_res.json()["id"]

        await seed_document_with_chunks(
            pg_engine,
            user_id=user_id,
            subject_id=subject_id,
            title="Vector Notes.pdf",
            texts=texts,
        )

        deck_res = await client.post(
            "/api/v1/flashcards/generate",
            json={"subject_id": subject_id, "card_count": 2},
            headers=headers,
        )
        assert deck_res.status_code == 201, deck_res.text

    return user_id, subject_id, deck_res.json()


async def test_flashcard_citations_point_at_seeded_chunks(pg_engine):
    """Every card must cite a real chunk owned by the requesting user."""
    _, subject_id, deck = await _real_user_with_deck(pg_engine)

    assert deck["subject_id"] == subject_id
    assert deck["card_count"] >= 1

    async with pg_engine() as session:
        result = await session.execute(
            select(Flashcard).where(Flashcard.deck_id == uuid.UUID(deck["id"]))
        )
        cards = list(result.scalars().all())
        assert cards

        for card in cards:
            assert card.document_id is not None
            assert card.chunk_id is not None
            assert card.document_title == "Vector Notes.pdf"
            assert card.source_snippet


async def test_deleting_a_document_nulls_the_citation_foreign_keys(pg_engine):
    """The FKs null out while the text snapshot survives, so the card stays usable."""
    _, _, deck = await _real_user_with_deck(pg_engine)
    card_id = uuid.UUID(deck["cards"][0]["id"])
    document_id = uuid.UUID(deck["cards"][0]["source_document_id"])

    async with pg_engine() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        await session.delete(document)
        await session.commit()

    async with pg_engine() as session:
        card = await session.get(Flashcard, card_id)
        assert card is not None
        # SET NULL applied.
        assert card.document_id is None
        assert card.chunk_id is None
        # The snapshot is what keeps the card meaningful.
        assert card.document_title == "Vector Notes.pdf"
        assert card.source_snippet
        assert card.front_text
        assert card.back_text


async def test_deleting_a_deck_cascades_to_its_cards_and_reviews(pg_engine):
    """A hard delete of a deck must not leave orphaned cards or reviews behind."""
    _, _, deck = await _real_user_with_deck(pg_engine)
    deck_id = uuid.UUID(deck["id"])

    async with pg_engine() as session:
        result = await session.execute(
            select(FlashcardDeck)
            .where(FlashcardDeck.id == deck_id)
            .options(selectinload(FlashcardDeck.cards))
        )
        stored = result.scalar_one()
        card_ids = [card.id for card in stored.cards]
        assert card_ids

        await session.delete(stored)
        await session.commit()

    async with pg_engine() as session:
        for card_id in card_ids:
            assert await session.get(Flashcard, card_id) is None

        result = await session.execute(
            select(FlashcardReview).where(FlashcardReview.deck_id == deck_id)
        )
        assert list(result.scalars().all()) == []
