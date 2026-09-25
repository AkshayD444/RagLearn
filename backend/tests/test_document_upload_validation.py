"""Validation coverage for ``POST /api/v1/documents/upload``.

The endpoint has three independent guards that must reject a bad upload
*before* anything is written to disk or to the database:

1. the file extension must be ``.pdf``,
2. the first five bytes must be the ``%PDF-`` magic number,
3. the payload must not exceed 20 MB.
"""

import io
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.v1.documents import MAX_FILE_SIZE_BYTES, UPLOAD_DIR
from app.models.document import Document
from tests.conftest import TestAsyncSessionLocal

PDF_HEADER = b"%PDF-"


def _pdf_bytes(size: int | None = None) -> bytes:
    """A payload with valid PDF magic bytes, optionally padded to ``size`` bytes."""
    if size is None:
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
    assert size > len(PDF_HEADER)
    return PDF_HEADER + b"0" * (size - len(PDF_HEADER))


async def _upload(client, headers, subject_id, *, filename, payload, content_type="application/pdf"):
    return await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject_id},
        files={"file": (filename, io.BytesIO(payload), content_type)},
        headers=headers,
    )


async def _documents_in(subject_id: str) -> list:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.subject_id == uuid.UUID(subject_id))
        )
        return list(result.scalars().all())


def _upload_dir_entries(user_id: str, subject_id: str) -> list:
    target = Path(UPLOAD_DIR) / str(user_id) / str(subject_id)
    if not target.is_dir():
        return []
    return sorted(p.name for p in target.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        "notes.txt",
        "notes.docx",
        "notes.pdf.txt",
        "notes.PDF.exe",
        "notes",
        "archive.zip",
    ],
)
async def test_non_pdf_extension_is_rejected(client, registered_user, subject, filename):
    """A missing or unexpected extension is refused before the magic-byte check."""
    res = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename=filename,
        payload=_pdf_bytes(),
    )
    assert res.status_code == 400
    assert "extension" in res.json()["detail"].lower()
    assert "pdf" in res.json()["detail"].lower()

    assert await _documents_in(subject) == []
    assert _upload_dir_entries(registered_user["user_id"], subject) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b"PK\x03\x04rest-of-a-zip-file",          # a real ZIP header
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,         # a PNG header
        b"GIF89a" + b"0" * 64,                    # a GIF header
        b"not a pdf at all",                      # plain text
        b"",                                      # empty body
        b"pdf-1.4 lowercase",                     # magic number is case sensitive
    ],
)
async def test_invalid_magic_bytes_are_rejected(client, registered_user, subject, payload):
    """A ``.pdf`` name is not enough: the body must start with ``%PDF-``."""
    res = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="pretend.pdf",
        payload=payload,
    )
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "magic bytes" in detail
    assert "corrupted" in detail

    assert await _documents_in(subject) == []
    assert _upload_dir_entries(registered_user["user_id"], subject) == []


@pytest.mark.asyncio
async def test_mismatched_extension_and_content_are_rejected(client, registered_user, subject):
    """A PNG named ``.png`` fails the extension guard, not the magic-byte guard."""
    res = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="diagram.png",
        payload=b"\x89PNG\r\n\x1a\n" + b"0" * 32,
        content_type="image/png",
    )
    assert res.status_code == 400
    assert "extension" in res.json()["detail"].lower()
    assert await _documents_in(subject) == []


@pytest.mark.asyncio
async def test_file_larger_than_20mb_is_rejected(client, registered_user, subject):
    """Anything above ``MAX_FILE_SIZE_BYTES`` is refused."""
    oversized = _pdf_bytes(MAX_FILE_SIZE_BYTES + 1)
    assert len(oversized) == 20 * 1024 * 1024 + 1

    res = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="huge.pdf",
        payload=oversized,
    )
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "20mb" in detail
    assert "exceeds" in detail

    assert await _documents_in(subject) == []
    assert _upload_dir_entries(registered_user["user_id"], subject) == []


@pytest.mark.asyncio
async def test_the_configured_limit_is_20mb():
    """The advertised limit must stay the 20 MB the error message promises."""
    assert MAX_FILE_SIZE_BYTES == 20 * 1024 * 1024


@pytest.mark.asyncio
async def test_size_limit_boundary(monkeypatch, client, registered_user, subject):
    """At the limit the upload is accepted; one byte over it is rejected.

    The limit is lowered so the boundary can be exercised without pushing
    20 MB of zeros through the ASGI transport on every run; the real 20 MB
    ceiling is covered by ``test_file_larger_than_20mb_is_rejected``.
    """
    import app.api.v1.documents as documents_api

    monkeypatch.setattr(documents_api, "MAX_FILE_SIZE_BYTES", 1024)

    at_limit = _pdf_bytes(1024)
    accepted = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="at-limit.pdf",
        payload=at_limit,
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["file_size_bytes"] == 1024
    await client.delete(
        f"/api/v1/documents/{accepted.json()['id']}", headers=registered_user["headers"]
    )

    over_limit = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="over-limit.pdf",
        payload=_pdf_bytes(1025),
    )
    assert over_limit.status_code == 400
    assert "exceeds" in over_limit.json()["detail"].lower()
    assert "20mb" in over_limit.json()["detail"].lower()


@pytest.mark.asyncio
async def test_size_check_runs_after_the_format_guards(client, registered_user, subject):
    """An oversized file with a bad extension reports the extension problem."""
    res = await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="huge.txt",
        payload=_pdf_bytes(MAX_FILE_SIZE_BYTES + 1024),
    )
    assert res.status_code == 400
    assert "extension" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_empty_filename_falls_back_to_the_default_pdf_name(
    client, registered_user, subject
):
    """A client that omits the filename still gets a default ``.pdf`` name.

    The part is written by hand because ``httpx`` emits a plain form field (not
    a file) when it is handed an empty filename.
    """
    boundary = "----phase9uploadvalidation"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="subject_id"\r\n\r\n',
            subject.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename=""\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            _pdf_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )

    res = await client.post(
        "/api/v1/documents/upload",
        content=body,
        headers={
            **registered_user["headers"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    assert res.status_code == 201, res.text
    payload = res.json()
    assert payload["title"] == "uploaded_document.pdf"
    await client.delete(
        f"/api/v1/documents/{payload['id']}", headers=registered_user["headers"]
    )


@pytest.mark.asyncio
async def test_file_part_without_a_filename_is_a_schema_error(
    client, registered_user, subject
):
    """A part with no ``filename`` is not an ``UploadFile``, so FastAPI 422s."""
    boundary = "----phase9nofilename"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="subject_id"\r\n\r\n',
            subject.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            _pdf_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    res = await client.post(
        "/api/v1/documents/upload",
        content=body,
        headers={
            **registered_user["headers"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    assert res.status_code == 422
    assert await _documents_in(subject) == []


@pytest.mark.asyncio
async def test_upload_requires_subject_id_and_file(client, registered_user, subject):
    """Missing form fields are schema errors, not silent successes."""
    missing_subject = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
        headers=registered_user["headers"],
    )
    assert missing_subject.status_code == 422

    missing_file = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject},
        headers=registered_user["headers"],
    )
    assert missing_file.status_code == 422

    assert await _documents_in(subject) == []


@pytest.mark.asyncio
async def test_subject_id_must_be_a_uuid(client, registered_user):
    res = await _upload(
        client,
        registered_user["headers"],
        "not-a-uuid",
        filename="notes.pdf",
        payload=_pdf_bytes(),
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_oversized_rejection_does_not_create_upload_directories(
    client, registered_user, subject
):
    """A rejected oversized upload must not leave an empty user directory behind."""
    await _upload(
        client,
        registered_user["headers"],
        subject,
        filename="huge.pdf",
        payload=_pdf_bytes(MAX_FILE_SIZE_BYTES + 1),
    )
    root = Path(UPLOAD_DIR) / registered_user["user_id"] / subject
    assert not root.is_dir() or list(root.iterdir()) == []
