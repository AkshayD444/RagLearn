"""B6: a document must never be stranded in PROCESSING.

Ingestion runs as a FastAPI ``BackgroundTask``, so it dies with the process. A
restart, a redeploy or an OOM kill in the middle of a large PDF used to leave the
row in ``PROCESSING`` with no worker that still intended to finish it -- and
``reprocess`` answers 409 for a PROCESSING document, so the owner had no way to
retry it at all.

These tests pin the guarantees that replace that:

* a successful run still ends READY with its chunks;
* a failing run still ends FAILED with its reason;
* documents left PROCESSING by a dead process are recovered at startup;
* recovering again changes nothing;
* READY and FAILED documents are left alone, with their chunks and their existing
  error messages intact;
* and none of it moves an ownership boundary.

The genuinely concurrent races -- two sweeps at once, two retries at once -- live
in tests/integration/test_document_recovery_concurrency.py, because SQLite
serialises every write onto a single connection and cannot observe them.
"""

import io
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.main import app as fastapi_app
from app.main import lifespan
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.document_processor import process_document_file
from app.services.document_recovery import (
    INTERRUPTED_PROCESSING_ERROR,
    recover_orphaned_documents,
)
from tests.conftest import (
    TestAsyncSessionLocal,
    seed_document_with_chunks,
    test_engine,
)


def _text_pdf_bytes() -> bytes:
    """A valid single-page PDF that yields extractable text."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 50 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (B6 Recovery Material) Tj ET\n"
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


def _blank_pdf_bytes() -> bytes:
    """A valid PDF with no text layer, so extraction fails rather than parsing."""
    import tempfile
    from pathlib import Path

    from pypdf import PdfWriter

    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with handle:
        writer.write(handle)
    data = Path(handle.name).read_bytes()
    os.remove(handle.name)
    return data


async def _upload(client, headers, subject_id, name, payload) -> dict:
    res = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject_id},
        files={"file": (name, io.BytesIO(payload), "application/pdf")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _document(document_id: str) -> Document:
    async with TestAsyncSessionLocal() as session:
        return await session.get(Document, uuid.UUID(str(document_id)))


async def _set_status(document_id, status: str, error_message=None) -> None:
    """Puts a document into a state a specific outcome would have left it in."""
    async with TestAsyncSessionLocal() as session:
        doc = await session.get(Document, uuid.UUID(str(document_id)))
        doc.status = status
        doc.error_message = error_message
        await session.commit()


async def _chunk_count(document_id) -> int:
    async with TestAsyncSessionLocal() as session:
        return await session.scalar(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == uuid.UUID(str(document_id))
            )
        )


async def _processing_document_ids() -> list:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(Document.id).where(Document.status == "PROCESSING")
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 1. The success path is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_processing_still_reaches_ready(client, registered_user, subject):
    """Validation -> extraction -> chunking -> embeddings -> chunks -> READY.

    Asserted on the upload's own background task, which has already run to
    completion by the time the test regains control -- so this is the real
    end-to-end path, not a hand-driven stand-in for it.
    """
    body = await _upload(
        client,
        registered_user["headers"],
        subject,
        "b6_ready.pdf",
        _text_pdf_bytes(),
    )
    document = await _document(body["id"])

    assert document.status == "READY"
    assert document.error_message is None
    assert await _chunk_count(body["id"]) >= 1
    # The recovery sweep must find nothing left to do after a clean run.
    assert await _processing_document_ids() == []

    status = await client.get(
        f"/api/v1/documents/{body['id']}/status", headers=registered_user["headers"]
    )
    assert status.json()["status"] == "READY"
    assert status.json()["error_message"] is None


# ---------------------------------------------------------------------------
# 2. The failure path is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processing_failure_still_reaches_failed_with_its_reason(
    client, registered_user, subject
):
    body = await _upload(
        client, registered_user["headers"], subject, "b6_blank.pdf", _blank_pdf_bytes()
    )
    document = await _document(body["id"])

    await process_document_file(
        document_id=document.id,
        file_path=document.file_path,
        subject_id=document.subject_id,
        user_id=document.user_id,
    )

    failed = await _document(body["id"])
    assert failed.status == "FAILED"
    # The real reason is preserved, not replaced by a generic one.
    assert "no extractable text" in failed.error_message.lower()
    assert await _processing_document_ids() == []


@pytest.mark.asyncio
async def test_a_cancelled_run_records_the_interruption_instead_of_staying_stuck(
    registered_user, subject, monkeypatch
):
    """A graceful shutdown is the interruption the app can still write about.

    SIGTERM during a rolling deploy cancels the background task.
    ``asyncio.CancelledError`` is not an ``Exception``, so the generic handler
    never saw it and the document stayed in PROCESSING with nothing running --
    the same trap as a crash, on the deployment path that is supposed to be the
    graceful one.
    """
    import asyncio
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    with handle:
        handle.write(_text_pdf_bytes())

    document_id = uuid.uuid4()
    async with TestAsyncSessionLocal() as session:
        session.add(
            Document(
                id=document_id,
                subject_id=uuid.UUID(subject),
                user_id=uuid.UUID(registered_user["user_id"]),
                title="Cancelled.pdf",
                file_path=handle.name,
                file_size_bytes=os.path.getsize(handle.name),
                status="PROCESSING",
            )
        )
        await session.commit()

    reached_embeddings = asyncio.Event()

    class _NeverReturns:
        """Stands in for the embedding call, which will not finish in time."""

        async def get_embeddings_batch(self, texts):
            reached_embeddings.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("app.rag.document_processor.embedding_service", _NeverReturns())

    try:
        task = asyncio.ensure_future(
            process_document_file(
                document_id=document_id,
                file_path=handle.name,
                subject_id=uuid.UUID(subject),
                user_id=uuid.UUID(registered_user["user_id"]),
            )
        )
        await asyncio.wait_for(reached_embeddings.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        os.remove(handle.name)

    cancelled = await _document(str(document_id))
    assert cancelled.status == "FAILED"
    assert cancelled.error_message == INTERRUPTED_PROCESSING_ERROR
    assert await _chunk_count(document_id) == 0
    assert await _processing_document_ids() == []


# ---------------------------------------------------------------------------
# 3. Startup recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_recovery_unsticks_orphaned_processing_documents(
    client, registered_user, subject, monkeypatch
):
    """The lifespan sweep is what actually frees a document stranded by a crash."""
    body = await _upload(
        client,
        registered_user["headers"],
        subject,
        "b6_interrupted.pdf",
        _text_pdf_bytes(),
    )
    document = await _document(body["id"])
    # Stand in for a process that died mid-ingestion. The upload's background
    # task has already run to completion under the test client, so the row has to
    # be put back into the state a killed process would have left it in.
    await _set_status(document.id, "PROCESSING")

    # Drive the real lifespan. Only the process globals are redirected:
    # ensure_schema is stubbed because the test database already holds the
    # application's tables without an alembic_version stamp, and refusing to
    # start is the correct behaviour for *that* state -- it is covered by
    # tests/test_migrations.py, not here.
    monkeypatch.setattr("app.main.engine", test_engine)
    monkeypatch.setattr("app.main.ensure_schema", lambda connection: None)
    monkeypatch.setattr(
        "app.services.document_recovery.AsyncSessionLocal", TestAsyncSessionLocal
    )
    async with lifespan(fastapi_app):
        pass

    recovered = await _document(body["id"])
    assert recovered.status == "FAILED"
    assert recovered.error_message == INTERRUPTED_PROCESSING_ERROR
    assert "interrupted" in recovered.error_message.lower()

    # And the owner can now read the reason through the API.
    status = await client.get(
        f"/api/v1/documents/{body['id']}/status", headers=registered_user["headers"]
    )
    assert status.json()["status"] == "FAILED"
    assert status.json()["error_message"]


@pytest.mark.asyncio
async def test_recovery_reports_the_documents_it_moved(client, registered_user, subject):
    body = await _upload(
        client, registered_user["headers"], subject, "b6_reported.pdf", _text_pdf_bytes()
    )
    document = await _document(body["id"])
    await _set_status(document.id, "PROCESSING")

    recovered = await recover_orphaned_documents(TestAsyncSessionLocal)

    assert document.id in recovered
    assert (await _document(body["id"])).status == "FAILED"


@pytest.mark.asyncio
async def test_recovery_makes_a_recovered_document_reprocessable(
    client, registered_user, subject
):
    """The point of FAILED rather than a new status: retry works again.

    Before this, a document stranded in PROCESSING answered 409 to reprocess
    forever -- the owner could only delete it and upload the PDF a second time.
    """
    body = await _upload(
        client, registered_user["headers"], subject, "b6_retry.pdf", _text_pdf_bytes()
    )
    document = await _document(body["id"])
    await _set_status(document.id, "PROCESSING")

    assert await recover_orphaned_documents(TestAsyncSessionLocal)

    retry = await client.post(
        f"/api/v1/documents/{body['id']}/reprocess", headers=registered_user["headers"]
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "PROCESSING"

    document = await _document(body["id"])
    await process_document_file(
        document_id=document.id,
        file_path=document.file_path,
        subject_id=document.subject_id,
        user_id=document.user_id,
    )
    assert (await _document(body["id"])).status == "READY"


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_recovery_is_a_no_op(client, registered_user, subject):
    body = await _upload(
        client, registered_user["headers"], subject, "b6_twice.pdf", _text_pdf_bytes()
    )
    document = await _document(body["id"])
    await _set_status(document.id, "PROCESSING")

    first = await recover_orphaned_documents(TestAsyncSessionLocal)
    after_first = await _document(body["id"])
    second = await recover_orphaned_documents(TestAsyncSessionLocal)
    third = await recover_orphaned_documents(TestAsyncSessionLocal)

    assert first == [document.id]
    assert second == [] and third == []

    # The later runs must not rewrite the row either: same status, same message,
    # no churn for anything watching the row.
    after_third = await _document(body["id"])
    assert after_third.status == after_first.status
    assert after_third.error_message == after_first.error_message


@pytest.mark.asyncio
async def test_recovery_leaves_ready_documents_alone(registered_user, subject):
    document_id, chunk_ids = await seed_document_with_chunks(
        TestAsyncSessionLocal,
        user_id=registered_user["user_id"],
        subject_id=subject,
        title="Already Ready.pdf",
        texts=["Ready material that must survive recovery."],
        status="READY",
    )

    recovered = await recover_orphaned_documents(TestAsyncSessionLocal)

    assert recovered == []
    document = await _document(document_id)
    assert document.status == "READY"
    assert document.error_message is None
    assert await _chunk_count(document_id) == len(chunk_ids)


@pytest.mark.asyncio
async def test_recovery_keeps_an_existing_failure_reason(registered_user, subject):
    """A real extraction failure must not be overwritten by the sweep."""
    document_id, _ = await seed_document_with_chunks(
        TestAsyncSessionLocal,
        user_id=registered_user["user_id"],
        subject_id=subject,
        title="Already Failed.pdf",
        status="FAILED",
    )
    await _set_status(document_id, "FAILED", "No extractable text found in PDF document.")

    recovered = await recover_orphaned_documents(TestAsyncSessionLocal)

    assert recovered == []
    document = await _document(document_id)
    assert document.status == "FAILED"
    assert document.error_message == "No extractable text found in PDF document."


@pytest.mark.asyncio
async def test_recovery_drops_partial_chunks_from_an_interrupted_run(
    registered_user, subject
):
    """A recovered document starts clean, so a retry cannot index on top.

    The processor commits its chunks together with the READY transition, so this
    is not a state the current pipeline reaches. It is still the state a future
    commit boundary could leave, and inheriting those embeddings would put
    superseded text into search results.
    """
    document_id, chunk_ids = await seed_document_with_chunks(
        TestAsyncSessionLocal,
        user_id=registered_user["user_id"],
        subject_id=subject,
        title="Partially Indexed.pdf",
        texts=["Half-written chunk from a killed run."],
        status="PROCESSING",
    )
    assert await _chunk_count(document_id) == len(chunk_ids)

    recovered = await recover_orphaned_documents(TestAsyncSessionLocal)

    assert recovered == [uuid.UUID(document_id)]
    assert await _chunk_count(document_id) == 0


# ---------------------------------------------------------------------------
# 5. Ownership is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_does_not_change_who_can_see_a_document(
    client, registered_user, other_user, subject, forget_session
):
    body = await _upload(
        client, registered_user["headers"], subject, "b6_private.pdf", _text_pdf_bytes()
    )
    document = await _document(body["id"])
    await _set_status(document.id, "PROCESSING")

    await recover_orphaned_documents(TestAsyncSessionLocal)

    owner_view = await client.get(
        f"/api/v1/documents/{body['id']}", headers=registered_user["headers"]
    )
    assert owner_view.status_code == 200
    assert owner_view.json()["status"] == "FAILED"

    denied = await client.get(
        f"/api/v1/documents/{body['id']}", headers=other_user["headers"]
    )
    assert denied.status_code == 404

    forget_session(client)
    anonymous = await client.get(f"/api/v1/documents/{body['id']}")
    assert anonymous.status_code == 401

    # And the intruder still cannot retry it.
    anonymous_retry = await client.post(f"/api/v1/documents/{body['id']}/reprocess")
    assert anonymous_retry.status_code == 401


@pytest.mark.asyncio
async def test_recovery_sweeps_every_owner_not_just_one(
    client, registered_user, other_user, subject
):
    """The sweep is not user-scoped, so no owner's document is left stranded."""
    theirs_res = await client.post(
        "/api/v1/subjects",
        json={"title": "Other Student Notes", "description": "Their subject"},
        headers=other_user["headers"],
    )
    assert theirs_res.status_code == 201, theirs_res.text
    their_subject = theirs_res.json()["id"]

    mine = await _upload(
        client, registered_user["headers"], subject, "b6_mine.pdf", _text_pdf_bytes()
    )
    theirs = await _upload(
        client, other_user["headers"], their_subject, "b6_theirs.pdf", _text_pdf_bytes()
    )
    await _set_status(mine["id"], "PROCESSING")
    await _set_status(theirs["id"], "PROCESSING")

    recovered = await recover_orphaned_documents(TestAsyncSessionLocal)

    assert sorted(recovered) == sorted([uuid.UUID(mine["id"]), uuid.UUID(theirs["id"])])
    assert (await _document(mine["id"])).status == "FAILED"
    assert (await _document(theirs["id"])).status == "FAILED"