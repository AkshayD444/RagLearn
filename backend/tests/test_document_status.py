"""Document processing status and reprocessing.

Two guarantees matter here and are asserted directly:

* a retry reuses the existing document row, so the subject never ends up
  showing the same PDF twice;
* a retry cannot be launched while one is already in flight, so a double click
  cannot start two extractions against the same document.
"""

import io
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.document_processor import process_document_file
from app.rag.embeddings import embedding_service
from tests.conftest import TestAsyncSessionLocal


def _text_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 50 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (Artificial Intelligence RAG Notes) Tj ET\n"
        b"endstream\n"
        b"endobj\n"
        b"xref\n"
        b"0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000280 00000 n \n"
        b"trailer << /Size 5 /Root 1 0 R >>\n"
        b"startxref\n"
        b"381\n"
        b"%%EOF\n"
    )


async def _upload(client, headers, subject_id: str, filename: str = "lecture.pdf") -> str:
    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject_id},
        files={"file": (filename, io.BytesIO(_text_pdf_bytes()), "application/pdf")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _document(document_id: str) -> Document:
    async with TestAsyncSessionLocal() as session:
        return await session.get(Document, uuid.UUID(document_id))


async def _document_count(subject_id: str) -> int:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(Document.id)).where(
                Document.subject_id == uuid.UUID(subject_id)
            )
        )
        return result.scalar_one()


async def _chunk_count(document_id: str) -> int:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == uuid.UUID(document_id)
            )
        )
        return result.scalar_one()


async def _add_stale_chunks(document_id: str, count: int = 2) -> None:
    """Attaches chunks from a previous run, as a partial reprocess would leave."""

    async with TestAsyncSessionLocal() as session:
        doc = await session.get(Document, uuid.UUID(document_id))
        for i in range(1, count + 1):
            text = f"Stale chunk {i} that must not survive a reprocess."
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    subject_id=doc.subject_id,
                    user_id=doc.user_id,
                    chunk_index=i,
                    page_number=i,
                    token_count=8,
                    content=text,
                    embedding=await embedding_service.get_embedding(text),
                )
            )
        await session.commit()


async def _mark_failed(document_id: str, message: str = "No extractable text found.") -> None:
    async with TestAsyncSessionLocal() as session:
        doc = await session.get(Document, uuid.UUID(document_id))
        doc.status = "FAILED"
        doc.error_message = message
        await session.commit()


async def _mark_processing(document_id: str) -> None:
    """Puts a document back into PROCESSING to stand in for a job in flight.

    Upload already runs its background task by the time the test regains
    control, so an in-flight document has to be staged explicitly.
    """

    async with TestAsyncSessionLocal() as session:
        doc = await session.get(Document, uuid.UUID(document_id))
        doc.status = "PROCESSING"
        doc.error_message = None
        await session.commit()


async def _chunk_contents(document_id: str) -> list[str]:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(DocumentChunk.content).where(
                DocumentChunk.document_id == uuid.UUID(document_id)
            )
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_status_reports_processing_then_ready(client, registered_user, subject):
    document_id = await _upload(client, registered_user["headers"], subject)

    initial = await client.get(
        f"/api/v1/documents/{document_id}/status", headers=registered_user["headers"]
    )
    assert initial.status_code == 200
    assert initial.json()["status"] in {"PROCESSING", "READY"}
    assert initial.json()["id"] == document_id
    assert initial.json()["subject_id"] == subject
    # A narrow payload: no title, no file path, just what a poll loop needs.
    assert "title" not in initial.json()
    assert "file_path" not in initial.json()

    document = await _document(document_id)
    await process_document_file(
        document_id=document.id,
        file_path=document.file_path,
        subject_id=document.subject_id,
        user_id=document.user_id,
    )

    ready = await client.get(
        f"/api/v1/documents/{document_id}/status", headers=registered_user["headers"]
    )
    assert ready.json()["status"] == "READY"
    assert ready.json()["chunk_count"] >= 1
    assert ready.json()["error_message"] is None


@pytest.mark.asyncio
async def test_status_surfaces_the_failure_reason(client, registered_user, subject, seeded_chunks):
    document_id, _ = await seeded_chunks(["Whatever."], title="Broken.pdf")
    await _mark_failed(document_id, "Uploaded file not found at path: '/gone.pdf'")

    res = await client.get(
        f"/api/v1/documents/{document_id}/status", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json()["status"] == "FAILED"
    assert "/gone.pdf" in res.json()["error_message"]


@pytest.mark.asyncio
async def test_status_is_owner_scoped(client, registered_user, other_user, seeded_chunks):
    document_id, _ = await seeded_chunks(["Private material."], title="Private.pdf")

    denied = await client.get(
        f"/api/v1/documents/{document_id}/status", headers=other_user["headers"]
    )
    assert denied.status_code == 404

    anonymous = await client.get(f"/api/v1/documents/{document_id}/status")
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_reprocess_restores_a_failed_document_without_duplicating_it(
    client, registered_user, subject
):
    document_id = await _upload(client, registered_user["headers"], subject, "retry_me.pdf")
    await _add_stale_chunks(document_id)
    await _mark_failed(document_id, "No extractable text found in PDF document.")
    assert await _document_count(subject) == 1
    # The upload's own extraction left a chunk behind; adding stale ones makes
    # the "replaced, not accumulated" assertion below meaningful.
    before = await _chunk_contents(document_id)
    assert sum("Stale chunk" in text for text in before) == 2

    res = await client.post(
        f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json()["status"] == "PROCESSING"
    # The stale reason is cleared, and the chunks the response describes are the
    # only ones: superseded embeddings are not queryable during the retry.
    assert res.json()["error_message"] is None
    assert res.json()["chunk_count"] == 0
    assert (await _document(document_id)).id == uuid.UUID(document_id)

    # The background task runs after the response; drive it to completion.
    document = await _document(document_id)
    await process_document_file(
        document_id=document.id,
        file_path=document.file_path,
        subject_id=document.subject_id,
        user_id=document.user_id,
    )

    final = await client.get(
        f"/api/v1/documents/{document_id}/status", headers=registered_user["headers"]
    )
    assert final.json()["status"] == "READY"
    assert final.json()["chunk_count"] >= 1
    # The previous run's chunks are gone, replaced rather than accumulated.
    contents = await _chunk_contents(document_id)
    assert not any("Stale chunk" in text for text in contents)
    # Same row throughout: no duplicate was created for the retry.
    assert await _document_count(subject) == 1

    listed = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    assert [d["id"] for d in listed.json()] == [document_id]


@pytest.mark.asyncio
async def test_reprocess_while_processing_is_rejected(client, registered_user, subject):
    document_id = await _upload(client, registered_user["headers"], subject)
    await _mark_processing(document_id)

    res = await client.post(
        f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
    )
    assert res.status_code == 409
    assert "already being processed" in res.json()["detail"]


@pytest.mark.asyncio
async def test_reprocess_a_ready_document_reruns_it(client, registered_user, subject):
    document_id = await _upload(client, registered_user["headers"], subject)
    document = await _document(document_id)
    await process_document_file(
        document_id=document.id,
        file_path=document.file_path,
        subject_id=document.subject_id,
        user_id=document.user_id,
    )
    chunks_before = await _chunk_count(document_id)
    assert chunks_before >= 1

    res = await client.post(
        f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json()["status"] == "PROCESSING"

    document = await _document(document_id)
    await process_document_file(
        document_id=document.id,
        file_path=document.file_path,
        subject_id=document.subject_id,
        user_id=document.user_id,
    )
    # Re-indexing replaces the chunks rather than accumulating a second set.
    assert await _chunk_count(document_id) == chunks_before
    assert await _document_count(subject) == 1


@pytest.mark.asyncio
async def test_reprocess_reports_a_missing_file_instead_of_a_silent_failure(
    client, registered_user, subject, seeded_chunks
):
    document_id, _ = await seeded_chunks(["Material."], title="Vanished.pdf")
    await _mark_failed(document_id, "Uploaded file not found.")

    document = await _document(document_id)
    document.file_path = os.path.join("nonexistent-dir", "vanished.pdf")
    async with TestAsyncSessionLocal() as session:
        session.add(document)
        await session.commit()

    res = await client.post(
        f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
    )
    assert res.status_code == 400
    assert "upload" in res.json()["detail"].lower()
    # The failure is left intact so the document still explains itself.
    assert (await _document(document_id)).status == "FAILED"


@pytest.mark.asyncio
async def test_reprocess_is_owner_scoped(client, registered_user, other_user, seeded_chunks):
    document_id, chunk_ids = await seeded_chunks(
        ["Private material."], title="Private.pdf"
    )
    await _mark_failed(document_id, "No extractable text found.")
    path = (await _document(document_id)).file_path
    if path and os.path.exists(path):
        os.remove(path)

    denied = await client.post(
        f"/api/v1/documents/{document_id}/reprocess", headers=other_user["headers"]
    )
    assert denied.status_code == 404

    anonymous = await client.post(f"/api/v1/documents/{document_id}/reprocess")
    assert anonymous.status_code == 401

    # The owner's document is untouched by the intruder's attempt.
    owner_document = await _document(document_id)
    assert owner_document.status == "FAILED"
    assert await _chunk_count(document_id) == len(chunk_ids)


@pytest.mark.asyncio
async def test_reprocess_unknown_document_is_404(client, registered_user):
    res = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/reprocess", headers=registered_user["headers"]
    )
    assert res.status_code == 404
