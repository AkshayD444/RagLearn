import asyncio
import uuid
import os
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.logging_config import get_logger
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.text_splitter import RecursiveTextSplitter
from app.rag.embeddings import embedding_service
from app.services.document_recovery import INTERRUPTED_PROCESSING_ERROR

logger = get_logger("app.documents")


async def _mark_failed(
    session: AsyncSession, document_id: uuid.UUID, message: str
) -> None:
    """Records a terminal FAILED state so a document cannot sit in PROCESSING.

    Shared by the error path and the cancellation path below so both land on the
    same shape: ``status="FAILED"`` plus a non-null ``error_message`` the UI can
    show. The pending chunk inserts are rolled back first, because the session
    that failed may still be holding them.

    Best effort: this runs while something has already gone wrong, and a
    database that is refusing writes must not turn a recorded failure into an
    unhandled exception. If this write does not land, the row stays PROCESSING
    and the startup sweep in app/services/document_recovery.py picks it up on the
    next start.
    """
    try:
        await session.rollback()
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        failed_doc = result.scalar_one_or_none()
        if failed_doc:
            failed_doc.status = "FAILED"
            failed_doc.error_message = message
            await session.commit()
        logger.warning(
            "document_processing_failed",
            extra={"document_id": str(document_id), "error": message},
        )
    except Exception:
        logger.exception(
            "could_not_record_document_failure",
            extra={"document_id": str(document_id)},
        )


async def process_document_file(
    document_id: uuid.UUID,
    file_path: str,
    subject_id: uuid.UUID,
    user_id: uuid.UUID
):
    """Async background task that processes uploaded PDF documents into pgvector chunks."""
    async with AsyncSessionLocal() as session:
        # Fetch document
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            return

        try:
            actual_file_path = file_path or doc.file_path
            if not actual_file_path or not os.path.exists(actual_file_path):
                raise FileNotFoundError(f"Uploaded file not found at path: '{actual_file_path}'")

            # 1. PDF Text Extraction
            reader = PdfReader(actual_file_path)
            pages = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append({
                        "page_number": i + 1,
                        "text": text
                    })

            if not pages:
                raise ValueError("No extractable text found in PDF document.")

            # 2. Text Chunking
            splitter = RecursiveTextSplitter(chunk_size=800, chunk_overlap=100)
            chunks = splitter.split_pages(pages)

            if not chunks:
                raise ValueError("Document yielded 0 text chunks after processing.")

            # 3. Generate Embeddings
            # B1: this raises rather than substituting a hash-derived vector
            # when Gemini is unconfigured or failing (see app/rag/embeddings.py),
            # and the batch is all-or-nothing, so a failure here lands on the
            # handler below and the document is recorded as FAILED. The
            # `status = "READY"` assignment further down is therefore reachable
            # only with a full set of real, correctly-sized vectors behind it --
            # which is the point: a document that indexed noise is worse than one
            # that visibly failed, because READY looks like it worked.
            chunk_contents = [c["content"] for c in chunks]
            vectors = await embedding_service.get_embeddings_batch(chunk_contents)
            if len(vectors) != len(chunks):
                raise ValueError(
                    f"Embedding service returned {len(vectors)} vectors for "
                    f"{len(chunks)} chunks; refusing to index a partial result."
                )

            # 4. Store Chunks in DB
            for chunk_data, vector in zip(chunks, vectors):
                db_chunk = DocumentChunk(
                    document_id=document_id,
                    subject_id=subject_id,
                    user_id=user_id,
                    chunk_index=chunk_data["chunk_index"],
                    content=chunk_data["content"],
                    page_number=chunk_data["page_number"],
                    token_count=chunk_data["token_count"],
                    embedding=vector
                )
                session.add(db_chunk)

            # Update Document status to READY
            doc.status = "READY"
            doc.error_message = None
            await session.commit()

            logger.info(
                "document_processed",
                extra={
                    "document_id": str(document_id),
                    "chunks": len(chunks),
                },
            )

        except asyncio.CancelledError:
            # B6: a graceful shutdown (SIGTERM during a rolling deploy) cancels
            # this task, and CancelledError is not an Exception, so the generic
            # handler below never saw it -- the document was left in PROCESSING
            # with nothing left running. Recording the interruption first means a
            # rolling deploy does not strand uploads; a hard kill still cannot
            # write anything at all, which is what the startup sweep is for.
            await _mark_failed(session, document_id, INTERRUPTED_PROCESSING_ERROR)
            raise
        except Exception as e:
            await _mark_failed(session, document_id, str(e))