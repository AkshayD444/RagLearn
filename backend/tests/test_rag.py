import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import embedding_service
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


async def _create_subject(ac: AsyncClient, token: str, title: str = "RAG Test Subject"):
    res = await ac.post("/api/v1/subjects", json={"title": title}, headers=_auth(token))
    assert res.status_code == 201
    return res.json()["id"]


async def _seed_chunks(
    user_id: str, subject_id: str, title: str, texts: list
) -> tuple[str, list]:
    """Seeds a READY document with vectorizable chunks directly in the test DB."""
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

        result = await session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
        )
        chunk_ids = [str(c.id) for c in result.scalars().all()]

    return str(doc.id), chunk_ids


@pytest.mark.asyncio
async def test_rag_retrieval_returns_scoped_relevant_chunks():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "rag_retrieve@example.com")
        subject_id = await _create_subject(ac, token, "RAG Methods")

        ml_text = "Artificial intelligence systems learn patterns from large datasets."
        vector_text = "Vector databases store high-dimensional embeddings and support similarity search."
        doc_id, _ = await _seed_chunks(
            user_id, subject_id, "RAG Notes.pdf", [ml_text, vector_text]
        )

        res = await ac.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": subject_id, "query": vector_text, "top_k": 5},
            headers=_auth(token),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["subject_id"] == subject_id
        assert data["query"] == vector_text
        assert len(data["results"]) >= 1

        top = data["results"][0]
        assert top["document_id"] == doc_id
        assert top["document_title"] == "RAG Notes.pdf"
        assert top["chunk_index"] == 2
        assert top["page_number"] == 2
        assert "vector databases store" in top["content"].lower()
        assert top["score"] > 0.99


@pytest.mark.asyncio
async def test_rag_retrieval_and_ask_are_user_scoped():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a, user_a = await _register(ac, "rag_owner@example.com")
        token_b, user_b = await _register(ac, "rag_intruder@example.com")

        subject_a = await _create_subject(ac, token_a, "Owner Private Subject")
        await _seed_chunks(
            user_a, subject_a, "Secret Notes.pdf",
            ["Confidential details about neural network backpropagation."],
        )
        # Intruder's own subject with different content
        subject_b = await _create_subject(ac, token_b, "Intruder Subject")
        await _seed_chunks(
            user_b, subject_b, "Intruder Notes.pdf",
            ["Unrelated topic about botanical gardens."],
        )

        # Owner can retrieve their own data
        ok = await ac.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": subject_a, "query": "backpropagation"},
            headers=_auth(token_a),
        )
        assert ok.status_code == 200
        assert len(ok.json()["results"]) == 1
        assert ok.json()["results"][0]["document_title"] == "Secret Notes.pdf"

        # Intruder is blocked from the owner's subject on both endpoints
        for endpoint in ("/api/v1/rag/retrieve", "/api/v1/rag/ask"):
            res = await ac.post(
                endpoint,
                json={
                    "subject_id": subject_a,
                    "query" if endpoint.endswith("retrieve") else "question": "backpropagation",
                },
                headers=_auth(token_b),
            )
            assert res.status_code == 404, f"{endpoint} should reject cross-user access"
            assert "access denied" in res.json()["detail"]


@pytest.mark.asyncio
async def test_rag_ask_returns_grounded_answer_with_sources():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "rag_ask@example.com")
        subject_id = await _create_subject(ac, token, "Biology 201")

        text = "Photosynthesis converts sunlight into chemical energy stored in glucose molecules."
        doc_id, chunk_ids = await _seed_chunks(user_id, subject_id, "Bio Ch5.pdf", [text])

        res = await ac.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject_id, "question": "Explain photosynthesis briefly."},
            headers=_auth(token),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["subject_id"] == subject_id
        assert data["question"] == "Explain photosynthesis briefly."
        assert len(data["answer"]) > 0
        assert "Photosynthesis" in data["answer"]
        assert data["model_used"] == "deterministic-fallback"

        assert len(data["sources"]) == 1
        src = data["sources"][0]
        assert src["document_id"] == doc_id
        assert src["chunk_id"] == chunk_ids[0]
        assert src["document_title"] == "Bio Ch5.pdf"
        assert src["page_number"] == 1
        assert src["score"] >= 0.0

        # Sources must reference the same subject
        assert data["sources"][0]["chunk_id"] in chunk_ids


@pytest.mark.asyncio
async def test_rag_subject_without_indexed_material():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, _ = await _register(ac, "rag_empty@example.com")
        subject_id = await _create_subject(ac, token, "Empty Subject")

        # Retrieval on an empty subject returns no results (not an error)
        res = await ac.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": subject_id, "query": "anything"},
            headers=_auth(token),
        )
        assert res.status_code == 200
        assert res.json()["results"] == []

        # Asking on an empty subject is a clear 400
        res = await ac.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject_id, "question": "What is anything?"},
            headers=_auth(token),
        )
        assert res.status_code == 400
        assert "No indexed study materials" in res.json()["detail"]


@pytest.mark.asyncio
async def test_rag_validation_and_auth_errors():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token, user_id = await _register(ac, "rag_validate@example.com")
        subject_id = await _create_subject(ac, token, "Validation Subject")
        await _seed_chunks(user_id, subject_id, "Notes.pdf", ["Sample study content."])

        headers = _auth(token)

        # 401 without a token
        no_token = await ac.post(
            "/api/v1/rag/retrieve", json={"subject_id": subject_id, "query": "x"}
        )
        assert no_token.status_code == 401

        # 422 on blank question
        blank = await ac.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject_id, "question": ""},
            headers=headers,
        )
        assert blank.status_code == 422

        # 422 on out-of-range top_k
        bad_top = await ac.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject_id, "question": "question", "top_k": 0},
            headers=headers,
        )
        assert bad_top.status_code == 422

        bad_top = await ac.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": subject_id, "query": "query", "top_k": 21},
            headers=headers,
        )
        assert bad_top.status_code == 422

        # 404 on a random/foreign subject id
        foreign = await ac.post(
            "/api/v1/rag/retrieve",
            json={"subject_id": str(uuid.uuid4()), "query": "query"},
            headers=headers,
        )
        assert foreign.status_code == 404