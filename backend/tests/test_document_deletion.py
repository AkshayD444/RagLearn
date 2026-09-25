"""Deletion semantics for ``DELETE /api/v1/documents/{document_id}``.

A successful delete must remove three things: the document row, every vector
chunk that belongs to it, and the PDF on disk. Everything must be scoped to the
owner - another user gets a 404 and must not be able to delete anything.
"""

import os
import uuid

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from tests.conftest import TestAsyncSessionLocal, seed_document_with_chunks


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 0 >> stream\n\nendstream\n"
        b"endobj\n"
        b"trailer << /Size 5 /Root 1 0 R >>\n"
        b"startxref\n"
        b"381\n"
        b"%%EOF\n"
    )


async def _chunk_count(document_id: str) -> int:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == uuid.UUID(document_id)
            )
        )
        return result.scalar_one()


async def _document_row(document_id: str) -> Document | None:
    async with TestAsyncSessionLocal() as session:
        return await session.get(Document, uuid.UUID(document_id))


@pytest.mark.asyncio
async def test_deleting_a_document_removes_its_chunks(client, registered_user, subject, seeded_chunks):
    """Every vector chunk belonging to the document is gone after the delete."""
    document_id, chunk_ids = await seeded_chunks(
        ["Vector databases store embeddings for semantic search."],
        title="Chunk Owner.pdf",
    )
    assert len(chunk_ids) == 1
    assert await _chunk_count(document_id) == 1

    res = await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 204
    assert res.content == b""

    assert await _document_row(document_id) is None
    assert await _chunk_count(document_id) == 0

    # The document is no longer listable either.
    listed = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    assert listed.json() == []

    # A second delete is a 404, not a 500.
    again = await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_document_removes_the_stored_file(
    client, registered_user, subject
):
    """The PDF on disk is unlinked as part of the delete."""
    import io

    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject},
        files={"file": ("on-disk.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
        headers=registered_user["headers"],
    )
    assert res.status_code == 201, res.text
    document_id = res.json()["id"]

    document = await _document_row(document_id)
    file_path = document.file_path
    assert file_path and os.path.exists(file_path)

    deleted = await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert deleted.status_code == 204
    assert not os.path.exists(file_path)


@pytest.mark.asyncio
async def test_deletion_succeeds_when_the_file_is_already_gone(
    client, registered_user, subject, seeded_chunks
):
    """A missing file on disk must not turn the delete into a 500."""
    document_id, _ = await seeded_chunks(["Material to be deleted."], title="Ghost.pdf")

    document = await _document_row(document_id)
    document.file_path = "/nonexistent/path/ghost.pdf"

    res = await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 204
    assert await _document_row(document_id) is None


@pytest.mark.asyncio
async def test_cross_user_delete_returns_404_and_preserves_the_document(
    client, registered_user, subject, other_user, seeded_chunks
):
    """A second user cannot see or delete somebody else's document."""
    document_id, chunk_ids = await seeded_chunks(
        ["Private study material for their owner only."], title="Private.pdf"
    )
    file_path = (await _document_row(document_id)).file_path

    response = await client.delete(
        f"/api/v1/documents/{document_id}", headers=other_user["headers"]
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    # Nothing changed for the owner.
    assert await _document_row(document_id) is not None
    assert await _chunk_count(document_id) == len(chunk_ids)

    fetch = await client.get(
        f"/api/v1/documents/{document_id}", headers=other_user["headers"]
    )
    assert fetch.status_code == 404

    # The owner can still delete it.
    owner_delete = await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert owner_delete.status_code == 204
    assert await _document_row(document_id) is None
    if file_path:
        assert not os.path.exists(file_path)


@pytest.mark.asyncio
async def test_cross_user_cannot_list_another_users_documents(
    client, registered_user, subject, other_user, seeded_chunks
):
    """Listing is scoped to the owner, and a foreign subject id is a 404."""
    await seeded_chunks(["Owner only material."], title="Scoped.pdf")

    listed = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=other_user["headers"]
    )
    assert listed.status_code == 404

    own = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    assert own.status_code == 200
    assert len(own.json()) == 1


@pytest.mark.asyncio
async def test_deleting_one_document_keeps_the_others_intact(
    client, registered_user, subject, seeded_chunks
):
    """Chunks are removed per document, not per subject."""
    keep_id, keep_chunks = await seeded_chunks(
        ["Keep this material indexed."], title="Keep.pdf"
    )
    drop_id, drop_chunks = await seeded_chunks(
        ["Drop this material."], title="Drop.pdf"
    )
    assert keep_id != drop_id

    res = await client.delete(
        f"/api/v1/documents/{drop_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 204

    assert await _document_row(drop_id) is None
    assert await _chunk_count(drop_id) == 0

    assert await _document_row(keep_id) is not None
    assert await _chunk_count(keep_id) == len(keep_chunks)

    listed = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    assert [d["id"] for d in listed.json()] == [keep_id]


@pytest.mark.asyncio
async def test_chunk_count_drops_to_zero_in_the_listing_after_delete(
    client, registered_user, subject, seeded_chunks
):
    """The list endpoint reports chunk counts and stops reporting the document."""
    document_id, _ = await seeded_chunks(
        ["Vector databases store embeddings for semantic search."], title="Counted.pdf"
    )

    before = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    entry = next(d for d in before.json() if d["id"] == document_id)
    assert entry["chunk_count"] == 1
    assert entry["status"] == "READY"

    await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )

    after = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    assert all(d["id"] != document_id for d in after.json())


@pytest.mark.asyncio
async def test_deleted_document_chunks_are_no_longer_retrievable(
    client, registered_user, subject, seeded_chunks
):
    """The deleted document's text is unreachable through RAG retrieval."""
    from app.rag.retriever import retrieve_relevant_chunks

    document_id, _ = await seeded_chunks(
        ["Vector databases store embeddings for semantic search."],
        title="Retrievable.pdf",
    )

    async with TestAsyncSessionLocal() as session:
        before = await retrieve_relevant_chunks(
            session,
            query="vector databases",
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
            top_k=5,
        )
        assert [m["document_id"] for m in before] == [uuid.UUID(document_id)]

    await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )

    async with TestAsyncSessionLocal() as session:
        after = await retrieve_relevant_chunks(
            session,
            query="vector databases",
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
            top_k=5,
        )
        assert after == []

    asked = await client.post(
        "/api/v1/rag/ask",
        json={"subject_id": subject, "question": "vector databases"},
        headers=registered_user["headers"],
    )
    assert asked.status_code == 400
    assert "no indexed study materials" in asked.json()["detail"].lower()


@pytest.mark.asyncio
async def test_seed_helper_creates_removable_documents(
    registered_user, subject
):
    """``seed_document_with_chunks`` is the fixture the delete tests rely on."""
    document_id, chunk_ids = await seed_document_with_chunks(
        TestAsyncSessionLocal,
        user_id=registered_user["user_id"],
        subject_id=subject,
        title="Helper.pdf",
        texts=["One.", "Two.", "Three."],
    )
    assert len(chunk_ids) == 3
    assert await _chunk_count(document_id) == 3
