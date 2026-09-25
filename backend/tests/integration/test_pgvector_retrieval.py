"""Integration tests for the real PostgreSQL + pgvector retrieval path.

These are the only tests that exercise ``app.rag.retriever._retrieve_with_pgvector``
(the DB-side ``<=>`` cosine-distance operator) instead of the portable in-Python
fallback used by the SQLite suite.

Run them with the repository's Docker Compose database::

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
        python -m pytest -m postgres

Without ``TEST_DATABASE_URL`` (or when it is not a PostgreSQL URL) every test in
this module skips instantly, which keeps the default suite fast.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import embedding_service
from app.rag.retriever import _retrieve_with_pgvector, retrieve_relevant_chunks
from tests.conftest import (
    auth_headers,
    requires_postgres,
    seed_document_with_chunks,
    seed_user_and_subject,
    test_postgres_url as postgres_url,
)

pytestmark = pytest.mark.postgres

VECTOR_TEXT = "Vector databases store high-dimensional embeddings and support similarity search."
ML_TEXT = "Artificial intelligence systems learn statistical patterns from large datasets."
HISTORY_TEXT = "The Roman Republic relied on a Senate and popular assemblies for governance."


async def _register(client: AsyncClient, label: str) -> tuple[str, str]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{label}-{uuid.uuid4().hex[:10]}@example.com",
            "full_name": "Integration Student",
            "password": "password123",
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _create_subject(client: AsyncClient, token: str, title: str) -> str:
    res = await client.post(
        "/api/v1/subjects", json={"title": title}, headers=auth_headers(token)
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_pgvector_extension_is_available(pg_engine):
    """The `vector` extension must be installed for the Vector column to exist."""
    async with pg_engine() as session:
        result = await session.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        assert result.scalar_one_or_none() == "vector"

        # The chunk embedding column really is a pgvector `vector(768)` column.
        column_type = await session.execute(
            text(
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'document_chunks' AND a.attname = 'embedding'
                """
            )
        )
        assert column_type.scalar_one() == "vector(768)"


async def test_embeddings_round_trip_through_pgvector(pg_engine):
    """A 768-dim embedding is stored and read back without precision loss."""
    user_id, subject_id = await seed_user_and_subject(pg_engine)
    doc_id, chunk_ids = await seed_document_with_chunks(
        pg_engine,
        user_id=user_id,
        subject_id=subject_id,
        title="Round Trip.pdf",
        texts=[VECTOR_TEXT],
    )
    async with pg_engine() as session:
        chunk = await session.get(DocumentChunk, uuid.UUID(chunk_ids[0]))
        assert chunk is not None
        assert chunk.document_id == uuid.UUID(doc_id)
        assert len(chunk.embedding) == 768
        assert all(isinstance(value, float) for value in chunk.embedding)
        assert chunk.embedding == pytest.approx(
            await embedding_service.get_embedding(VECTOR_TEXT), abs=1e-6
        )


async def test_retrieval_uses_the_pgvector_distance_operator(pg_engine):
    """`retrieve_relevant_chunks` must dispatch to the pgvector path on PostgreSQL."""
    user_id, subject_id = await seed_user_and_subject(pg_engine)
    await seed_document_with_chunks(
        pg_engine,
        user_id=user_id,
        subject_id=subject_id,
        title="Operator Check.pdf",
        texts=[VECTOR_TEXT, ML_TEXT],
    )

    async with pg_engine() as session:
        assert session.bind.dialect.name == "postgresql"

        query_vector = await embedding_service.get_embedding(VECTOR_TEXT)
        matches = await _retrieve_with_pgvector(session, query_vector, subject_id, user_id, 5)
        assert len(matches) == 2
        # An exact match against a stored vector has distance 0 -> score 1.0
        assert matches[0]["score"] == pytest.approx(1.0, abs=1e-4)
        assert matches[0]["content"] == VECTOR_TEXT
        assert all(0.0 <= match["score"] <= 1.0 for match in matches)

        # Same result through the public entry point.
        via_api = await retrieve_relevant_chunks(
            session,
            query=VECTOR_TEXT,
            subject_id=subject_id,
            user_id=user_id,
            top_k=2,
        )
        assert [m["chunk_id"] for m in via_api] == [m["chunk_id"] for m in matches]


async def test_pgvector_ranks_by_cosine_distance(pg_engine):
    """The closest stored vector is returned first and `top_k` is respected."""
    user_id, subject_id = await seed_user_and_subject(pg_engine)
    await seed_document_with_chunks(
        pg_engine,
        user_id=user_id,
        subject_id=subject_id,
        title="Ranking.pdf",
        texts=[HISTORY_TEXT, VECTOR_TEXT, ML_TEXT],
    )

    async with pg_engine() as session:
        query_vector = await embedding_service.get_embedding(VECTOR_TEXT)
        matches = await retrieve_relevant_chunks(
            session, query=VECTOR_TEXT, subject_id=subject_id, user_id=user_id, top_k=1
        )
        assert len(matches) == 1
        assert matches[0]["content"] == VECTOR_TEXT
        assert matches[0]["document_title"] == "Ranking.pdf"
        assert matches[0]["score"] > 0.99

        # Scores are monotonically non-increasing as top_k grows.
        ranked = await retrieve_relevant_chunks(
            session, query=VECTOR_TEXT, subject_id=subject_id, user_id=user_id, top_k=3
        )
        scores = [match["score"] for match in ranked]
        assert scores == sorted(scores, reverse=True)
        assert ranked[0]["chunk_id"] == matches[0]["chunk_id"]


async def test_pgvector_retrieval_is_scoped_to_subject_and_user(pg_engine):
    """A user can never reach another user's or another subject's chunks."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token_a, user_a = await _register(client, "pg-owner")
        token_b, user_b = await _register(client, "pg-intruder")

        subject_a = await _create_subject(client, token_a, "Owner Subject")
        subject_a_2 = await _create_subject(client, token_a, "Owner Second Subject")
        subject_b = await _create_subject(client, token_b, "Intruder Subject")

        await seed_document_with_chunks(
            pg_engine, user_id=user_a, subject_id=subject_a, title="Owner.pdf", texts=[VECTOR_TEXT]
        )
        await seed_document_with_chunks(
            pg_engine,
            user_id=user_a,
            subject_id=subject_a_2,
            title="Owner Other.pdf",
            texts=[ML_TEXT],
        )
        await seed_document_with_chunks(
            pg_engine, user_id=user_b, subject_id=subject_b, title="Intruder.pdf", texts=[ML_TEXT]
        )

        # Cross-user access to the owner's subject is a 404.
        res = await client.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": subject_a, "query": "vector databases"},
            headers=auth_headers(token_b),
        )
        assert res.status_code == 404
        assert "access denied" in res.json()["detail"]

        # Direct retrieval is scoped to (subject_id, user_id) even without a subject check.
        async with pg_engine() as session:
            query_vector = await embedding_service.get_embedding(VECTOR_TEXT)

            foreign_user = await retrieve_relevant_chunks(
                session,
                query=VECTOR_TEXT,
                subject_id=subject_a,
                user_id=user_b,
                top_k=5,
            )
            assert foreign_user == []

            sibling_subject = await retrieve_relevant_chunks(
                session,
                query=VECTOR_TEXT,
                subject_id=subject_a_2,
                user_id=user_a,
                top_k=5,
            )
            assert all(match["content"] != VECTOR_TEXT for match in sibling_subject)

            owner_view = await retrieve_relevant_chunks(
                session, query=VECTOR_TEXT, subject_id=subject_a, user_id=user_a, top_k=5
            )
            assert [match["content"] for match in owner_view] == [VECTOR_TEXT]


async def test_rag_endpoints_serve_real_pgvector_results(pg_engine):
    """`/rag/retrieve` and `/rag/ask` serve results produced by the pgvector path."""
    requires_postgres(postgres_url())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token, user_id = await _register(client, "pg-rag")
        subject_id = await _create_subject(client, token, "Distributed Systems")

        doc_id, chunk_ids = await seed_document_with_chunks(
            pg_engine,
            user_id=user_id,
            subject_id=subject_id,
            title="Consensus.pdf",
            texts=[HISTORY_TEXT, VECTOR_TEXT],
        )

        res = await client.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": subject_id, "query": VECTOR_TEXT, "top_k": 5},
            headers=auth_headers(token),
        )
        assert res.status_code == 200, res.text
        results = res.json()["results"]
        assert results[0]["document_id"] == doc_id
        assert results[0]["chunk_id"] == chunk_ids[1]
        assert results[0]["document_title"] == "Consensus.pdf"
        assert results[0]["score"] > 0.99

        asked = await client.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject_id, "question": "Where are vectors stored?"},
            headers=auth_headers(token),
        )
        assert asked.status_code == 200, asked.text
        body = asked.json()
        assert body["model_used"] == "deterministic-fallback"
        assert body["sources"][0]["document_id"] == doc_id


async def test_chunks_with_null_embedding_are_skipped(pg_engine):
    """The pgvector query filters out rows without an embedding."""
    user_id, subject_id = await seed_user_and_subject(pg_engine)
    doc_id, chunk_ids = await seed_document_with_chunks(
        pg_engine, user_id=user_id, subject_id=subject_id, title="Sparse.pdf", texts=[VECTOR_TEXT]
    )

    async with pg_engine() as session:
        session.add(
            DocumentChunk(
                document_id=uuid.UUID(doc_id),
                subject_id=subject_id,
                user_id=user_id,
                chunk_index=99,
                page_number=99,
                token_count=3,
                content="An un-embedded chunk.",
                embedding=None,
            )
        )
        await session.commit()

        total = await session.execute(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.subject_id == subject_id)
        )
        assert total.scalar_one() == 2

        matches = await retrieve_relevant_chunks(
            session, query=VECTOR_TEXT, subject_id=subject_id, user_id=user_id, top_k=10
        )
        assert [match["content"] for match in matches] == [VECTOR_TEXT]


async def test_document_deletion_removes_chunks_via_postgres_cascade(pg_engine):
    """Deleting a document row removes its vector chunks and stored file."""
    requires_postgres(postgres_url())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token, user_id = await _register(client, "pg-delete")
        subject_id = await _create_subject(client, token, "Deletion Subject")

        doc_id, chunk_ids = await seed_document_with_chunks(
            pg_engine, user_id=user_id, subject_id=subject_id, title="Doomed.pdf", texts=[VECTOR_TEXT]
        )

        res = await client.delete(
            f"/api/v1/documents/{doc_id}", headers=auth_headers(token)
        )
        assert res.status_code == 204

        async with pg_engine() as session:
            assert await session.get(Document, uuid.UUID(doc_id)) is None
            remaining = await session.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.id.in_([uuid.UUID(cid) for cid in chunk_ids])
                )
            )
            assert remaining.scalar_one() == 0

        # Retrieval no longer sees the removed chunks.
        async with pg_engine() as session:
            matches = await retrieve_relevant_chunks(
                session, query=VECTOR_TEXT, subject_id=subject_id, user_id=uuid.UUID(user_id), top_k=5
            )
            assert matches == []


async def test_uuid_and_document_foreign_keys_work_on_postgres(pg_engine):
    """Sanity check that the asyncpg UUID mapping round-trips through the API."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token, user_id = await _register(client, "pg-uuid")
        subject_id = await _create_subject(client, token, "UUID Subject")

        res = await client.post(
            "/api/v1/documents/upload",
            data={"subject_id": subject_id},
            files={"file": ("notes.pdf", _minimal_pdf(), "application/pdf")},
            headers=auth_headers(token),
        )
        assert res.status_code == 201, res.text
        document_id = uuid.UUID(res.json()["id"])

        async with pg_engine() as session:
            doc = await session.get(Document, document_id)
            assert doc is not None
            assert doc.id == document_id
            assert doc.subject_id == uuid.UUID(subject_id)
            assert doc.user_id == uuid.UUID(user_id)
            assert isinstance(doc.id, uuid.UUID)

            typed = await session.execute(
                select(Document.id).where(Document.id == document_id)
            )
            assert typed.scalar_one() == document_id
            assert Document.__table__.c.id.type.as_uuid is True

        await client.delete(f"/api/v1/documents/{document_id}", headers=auth_headers(token))


def _minimal_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 50 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (Integration Test Material) Tj ET\n"
        b"endstream\n"
        b"endobj\n"
        b"trailer << /Size 5 /Root 1 0 R >>\n"
        b"%%EOF\n"
    )
