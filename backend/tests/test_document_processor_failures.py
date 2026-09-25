"""Failure-path coverage for the background document processor.

``process_document_file`` must never raise: every extraction, chunking, or
embedding error has to be recorded on the document row as ``status="FAILED"``
with a non-null ``error_message`` so the UI can explain the failure instead of
leaving a document stuck in ``PROCESSING`` forever.
"""

import io
import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.main import app
from app.models.document import Document
from app.rag.document_processor import process_document_file
from tests.conftest import TestAsyncSessionLocal, auth_headers


def _blank_pdf_bytes() -> bytes:
    """A structurally valid PDF with a single page that contains no text.

    ``pypdf`` happily parses it, so the processor reaches the "no extractable
    text" branch instead of failing to open the file.
    """
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with handle:
        writer.write(handle)
    data = Path(handle.name).read_bytes()
    os.remove(handle.name)
    return data


def _text_pdf_bytes() -> bytes:
    """A minimal but valid PDF that yields one extractable text page."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 50 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (Processor Regression Material) Tj ET\n"
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


async def _upload(client: AsyncClient, headers: dict, subject_id: str, name: str, payload: bytes) -> dict:
    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject_id},
        files={"file": (name, io.BytesIO(payload), "application/pdf")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _stored_document(document_id: str) -> Document:
    async with TestAsyncSessionLocal() as session:
        return await session.get(Document, uuid.UUID(document_id))


@pytest.mark.asyncio
async def test_zero_text_pdf_marks_document_failed_with_error_message(
    client, registered_user, subject
):
    """A parseable PDF with no text layer must be recorded as FAILED."""
    body = await _upload(
        client,
        registered_user["headers"],
        subject,
        "blank.pdf",
        _blank_pdf_bytes(),
    )
    document_id = body["id"]

    stored_path = (await _stored_document(document_id)).file_path
    assert os.path.exists(stored_path), "the uploader must have stored the file on disk"

    # The processor must not raise.
    await process_document_file(
        document_id=uuid.UUID(document_id),
        file_path=stored_path,
        subject_id=uuid.UUID(subject),
        user_id=uuid.UUID(registered_user["user_id"]),
    )

    document = await _stored_document(document_id)
    assert document.status == "FAILED"
    assert document.error_message is not None
    assert "no extractable text" in document.error_message.lower()

    # The failure must be visible through the API too.
    res = await client.get(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "FAILED"
    assert payload["error_message"]
    assert payload["chunk_count"] == 0

    listed = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    entry = next(d for d in listed.json() if d["id"] == document_id)
    assert entry["status"] == "FAILED"
    assert entry["error_message"]


@pytest.mark.asyncio
async def test_missing_file_on_disk_marks_document_failed(
    client, registered_user, subject
):
    """A file that disappeared between upload and processing must fail cleanly."""
    body = await _upload(
        client,
        registered_user["headers"],
        subject,
        "vanishing.pdf",
        _text_pdf_bytes(),
    )
    document_id = body["id"]

    stored = await _stored_document(document_id)
    os.remove(stored.file_path)
    assert not os.path.exists(stored.file_path)

    await process_document_file(
        document_id=uuid.UUID(document_id),
        file_path=stored.file_path,
        subject_id=uuid.UUID(subject),
        user_id=uuid.UUID(registered_user["user_id"]),
    )

    document = await _stored_document(document_id)
    assert document.status == "FAILED"
    assert document.error_message is not None
    assert "not found" in document.error_message.lower()
    assert stored.file_path in document.error_message


@pytest.mark.asyncio
async def test_empty_file_path_marks_document_failed(registered_user, subject):
    """``file_path=""`` falls back to the stored path; an empty one is a failure."""
    document_id = uuid.uuid4()
    async with TestAsyncSessionLocal() as session:
        session.add(
            Document(
                id=document_id,
                subject_id=uuid.UUID(subject),
                user_id=uuid.UUID(registered_user["user_id"]),
                title="No Path.pdf",
                file_path="",
                file_size_bytes=0,
                status="PROCESSING",
            )
        )
        await session.commit()

    await process_document_file(
        document_id=document_id,
        file_path="",
        subject_id=uuid.UUID(subject),
        user_id=uuid.UUID(registered_user["user_id"]),
    )

    document = await _stored_document(str(document_id))
    assert document.status == "FAILED"
    assert document.error_message is not None
    assert "not found" in document.error_message.lower()


@pytest.mark.asyncio
async def test_corrupt_pdf_body_marks_document_failed(
    client, registered_user, subject
):
    """Payload with valid magic bytes but a broken body must not stay PROCESSING."""
    corrupt = b"%PDF-1.4\n" + b"this is not a real pdf body at all" * 4
    body = await _upload(
        client, registered_user["headers"], subject, "corrupt.pdf", corrupt
    )
    document_id = body["id"]
    stored_path = (await _stored_document(document_id)).file_path

    await process_document_file(
        document_id=uuid.UUID(document_id),
        file_path=stored_path,
        subject_id=uuid.UUID(subject),
        user_id=uuid.UUID(registered_user["user_id"]),
    )

    document = await _stored_document(document_id)
    assert document.status == "FAILED"
    assert document.error_message


@pytest.mark.asyncio
async def test_successful_processing_clears_any_previous_error(
    client, registered_user, subject
):
    """A retry after a failure must reset error_message to NULL."""
    document_id = uuid.uuid4()
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    with handle:
        handle.write(_text_pdf_bytes())

    async with TestAsyncSessionLocal() as session:
        session.add(
            Document(
                id=document_id,
                subject_id=uuid.UUID(subject),
                user_id=uuid.UUID(registered_user["user_id"]),
                title="Retry.pdf",
                file_path=handle.name,
                file_size_bytes=os.path.getsize(handle.name),
                status="FAILED",
                error_message="No extractable text found in PDF document.",
            )
        )
        await session.commit()

    try:
        await process_document_file(
            document_id=document_id,
            file_path=handle.name,
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
        )

        document = await _stored_document(str(document_id))
        assert document.status == "READY"
        assert document.error_message is None
    finally:
        os.remove(handle.name)


@pytest.mark.asyncio
async def test_processor_is_a_no_op_for_an_unknown_document(registered_user, subject):
    """A deleted/unknown document id must not raise."""
    await process_document_file(
        document_id=uuid.uuid4(),
        file_path="/nonexistent/path/does-not-matter.pdf",
        subject_id=uuid.UUID(subject),
        user_id=uuid.UUID(registered_user["user_id"]),
    )


@pytest.mark.asyncio
async def test_failed_document_can_be_deleted_and_stays_deleted(
    client, registered_user, subject
):
    """A FAILED document is still deletable and leaves no chunks behind."""
    body = await _upload(
        client, registered_user["headers"], subject, "doomed-failure.pdf", _blank_pdf_bytes()
    )
    document_id = body["id"]
    stored_path = (await _stored_document(document_id)).file_path

    await process_document_file(
        document_id=uuid.UUID(document_id),
        file_path=stored_path,
        subject_id=uuid.UUID(subject),
        user_id=uuid.UUID(registered_user["user_id"]),
    )
    assert (await _stored_document(document_id)).status == "FAILED"

    res = await client.delete(
        f"/api/v1/documents/{document_id}", headers=registered_user["headers"]
    )
    assert res.status_code == 204
    assert await _stored_document(document_id) is None
    assert not os.path.exists(stored_path)


@pytest.mark.asyncio
async def test_upload_to_another_users_subject_is_rejected_before_processing(
    client, registered_user, subject, other_user
):
    """Ownership is checked before any file handling happens."""
    files = {"file": ("notes.pdf", io.BytesIO(_text_pdf_bytes()), "application/pdf")}
    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject},
        files=files,
        headers=other_user["headers"],
    )
    assert res.status_code == 404

    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.subject_id == uuid.UUID(subject))
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_document_upload_requires_authentication(client, subject):
    files = {"file": ("notes.pdf", io.BytesIO(_text_pdf_bytes()), "application/pdf")}
    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject},
        files=files,
    )
    assert res.status_code == 401

    anonymous = await client.get(f"/api/v1/documents/subject/{subject}")
    assert anonymous.status_code == 401

    random_id = await client.get(f"/api/v1/documents/{uuid.uuid4()}")
    assert random_id.status_code == 401

    missing_id = await client.delete(f"/api/v1/documents/{uuid.uuid4()}")
    assert missing_id.status_code == 401


@pytest.mark.asyncio
async def test_unknown_document_id_returns_404(client, registered_user):
    res = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}", headers=registered_user["headers"]
    )
    assert res.status_code == 404

    res = await client.delete(
        f"/api/v1/documents/{uuid.uuid4()}", headers=registered_user["headers"]
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_malformed_document_id_is_a_validation_error(client, registered_user):
    res = await client.get("/api/v1/documents/not-a-uuid", headers=registered_user["headers"])
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_document_routes_use_the_expected_status_codes(client, registered_user, subject):
    """Sanity check that the documents router is mounted where the frontend calls it."""
    res = await client.get(
        f"/api/v1/documents/subject/{subject}", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json() == []

    res = await client.get(
        f"/api/v1/documents/subject/{uuid.uuid4()}", headers=registered_user["headers"]
    )
    assert res.status_code == 404


async def test_app_exposes_documents_router():
    """The upload router is registered on the ASGI app under /api/v1."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        spec = (await ac.get("/api/v1/openapi.json")).json()
    assert "/api/v1/documents/upload" in spec["paths"]
    assert auth_headers("token")["Authorization"] == "Bearer token"
