"""B6 concurrency: recovery and retry claims on a real PostgreSQL.

SQLite hands every session the same connection (StaticPool) and serialises the
writes, so it structurally cannot show what these races do: two workers starting
at the same moment, or two retry requests arriving together. Both races are about
a *compare-and-set* on the document row, which is a property of the database
rather than of the Python code around it.

Two things could go wrong and neither is visible without this:

* if recovery were read-then-write, both workers would move the same row and the
  second would rewrite a row the first had already finished recovering;
* if the retry claim were read-then-write, both requests would enqueue a run and
  every chunk would be embedded and stored twice -- ``document_chunks`` has no
  uniqueness constraint to catch it.

Run them with::

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
        python -m pytest -m postgres

Without ``TEST_DATABASE_URL`` (or when it is not a PostgreSQL URL) every test in
this module skips instantly, which keeps the default suite fast.
"""

import asyncio
import io
import uuid

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.services.document_recovery import (
    INTERRUPTED_PROCESSING_ERROR,
    recover_orphaned_documents,
)
from tests.conftest import seed_document_with_chunks, seed_user_and_subject

pytestmark = pytest.mark.postgres


def _text_pdf_bytes() -> bytes:
    """A valid single-page PDF that yields extractable text."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 50 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (B6 Concurrency Material) Tj ET\n"
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


async def _add_processing_documents(pg_engine, count: int) -> list:
    """Seeds ``count`` documents owned by one real user, all stuck PROCESSING."""
    user_id, subject_id = await seed_user_and_subject(pg_engine)

    document_ids = []
    for index in range(count):
        document_id, _ = await seed_document_with_chunks(
            pg_engine,
            user_id=user_id,
            subject_id=subject_id,
            title=f"Stranded {index}.pdf",
            status="PROCESSING",
        )
        document_ids.append(uuid.UUID(document_id))

    return document_ids


async def _status_of(pg_engine, document_id) -> tuple:
    async with pg_engine() as session:
        document = await session.get(Document, document_id)
        return document.status, document.error_message


async def _chunk_count(pg_engine, document_id) -> int:
    async with pg_engine() as session:
        return await session.scalar(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == document_id
            )
        )


async def _upload_failed_document(client, headers, subject_id, pg_engine, name) -> uuid.UUID:
    """A real document with a real file on disk, sitting in FAILED.

    Reprocess refuses a document whose file is gone, so the upload endpoint is
    the only honest way to get one that a retry would actually accept.
    """
    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject_id},
        files={"file": (name, io.BytesIO(_text_pdf_bytes()), "application/pdf")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    document_id = uuid.UUID(res.json()["id"])

    async with pg_engine() as session:
        document = await session.get(Document, document_id)
        document.status = "FAILED"
        document.error_message = "No extractable text found in PDF document."
        await session.commit()

    return document_id


async def test_two_workers_starting_together_recover_each_document_once(pg_engine):
    """Exactly one of the two sweeps sees each row."""
    document_ids = await _add_processing_documents(pg_engine, 4)

    first, second = await asyncio.gather(
        recover_orphaned_documents(pg_engine),
        recover_orphaned_documents(pg_engine),
    )

    recovered = [document_id for batch in (first, second) for document_id in batch]
    # Once each, not twice: a document recovered twice would mean the second
    # worker rewrote a row the first had already finished with.
    assert sorted(recovered) == sorted(document_ids)

    for document_id in document_ids:
        status, error_message = await _status_of(pg_engine, document_id)
        assert status == "FAILED"
        assert error_message == INTERRUPTED_PROCESSING_ERROR


async def test_repeated_recovery_across_restarts_is_stable(pg_engine):
    """Five starts in a row change nothing after the first one."""
    document_id = (await _add_processing_documents(pg_engine, 1))[0]

    assert len(await recover_orphaned_documents(pg_engine)) == 1
    after_first = await _status_of(pg_engine, document_id)

    for _ in range(4):
        assert await recover_orphaned_documents(pg_engine) == []

    assert await _status_of(pg_engine, document_id) == after_first


async def test_concurrent_recovery_does_not_start_duplicate_processing(
    pg_engine, monkeypatch
):
    """Recovery marks rows; it never enqueues work, so it cannot duplicate it."""
    document_ids = await _add_processing_documents(pg_engine, 3)

    started: list[uuid.UUID] = []

    async def _spy(**kwargs):
        started.append(kwargs["document_id"])

    monkeypatch.setattr("app.rag.document_processor.process_document_file", _spy)
    await asyncio.gather(
        recover_orphaned_documents(pg_engine),
        recover_orphaned_documents(pg_engine),
    )

    assert started == []
    for document_id in document_ids:
        assert await _chunk_count(pg_engine, document_id) == 0


async def test_simultaneous_reprocess_requests_start_exactly_one_run(
    pg_engine, client, registered_user, subject
):
    """The reprocess claim is a compare-and-set, not a read-then-write.

    Two requests in the same instant both used to read a FAILED row, both set it
    to PROCESSING and both enqueue a background run -- which duplicates every
    chunk, because document_chunks has no uniqueness constraint to catch it.
    """
    document_id = await _upload_failed_document(
        client, registered_user["headers"], subject, pg_engine, "double_click.pdf"
    )

    first, second = await asyncio.gather(
        client.post(
            f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
        ),
        client.post(
            f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
        ),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    rejected = first if first.status_code == 409 else second
    assert "already being processed" in rejected.json()["detail"]


async def test_a_retry_and_a_startup_recovery_never_both_act(
    pg_engine, client, registered_user, subject
):
    """Recovery and a retry racing must not both act on the same row."""
    document_id = await _add_processing_documents(pg_engine, 1)
    document_id = document_id[0]

    recovered, retry = await asyncio.gather(
        recover_orphaned_documents(pg_engine),
        client.post(
            f"/api/v1/documents/{document_id}/reprocess", headers=registered_user["headers"]
        ),
    )

    # Whoever moved the row first closed the other one's window. Which one that
    # is depends on timing; that they never overlap does not.
    assert len(recovered) == (0 if retry.status_code == 200 else 1)

    status, error_message = await _status_of(pg_engine, document_id)
    if retry.status_code == 200:
        assert status == "PROCESSING"
        assert error_message is None
    else:
        assert status == "FAILED"
        assert error_message == INTERRUPTED_PROCESSING_ERROR