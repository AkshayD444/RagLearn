"""Startup recovery for documents stranded in ``PROCESSING``.

The ingestion pipeline runs as a FastAPI ``BackgroundTask``: extraction,
chunking and embedding all execute inside the API process after the upload
response has been sent. Nothing about that arrangement survives the process
dying. A restart, a redeploy or an OOM kill in the middle of a large PDF leaves
the row exactly as the upload wrote it -- ``status="PROCESSING"`` -- with no
worker anywhere that still intends to finish it. ``reprocess`` then refuses the
document with 409 ("already being processed"), so the only way out was deleting
and re-uploading it.

This module is the durable half of the answer, and it deliberately introduces no
queue. Every document still ``PROCESSING`` at startup is moved to ``FAILED`` with
a message that says what happened, which is the existing status model's
recoverable state: the owner sees a real reason instead of a spinner, and the
``reprocess`` endpoint will accept it again.

Two properties are load-bearing:

* **Compare-and-set.** The transition is a single conditional ``UPDATE ...
  WHERE status = 'PROCESSING'``. Whoever loses the race updates zero rows, so a
  document is recovered once no matter how many workers start, how often startup
  runs, or how the sweep overlaps a manual ``reprocess``. Recovery cannot start
  work, so it also cannot duplicate it.
* **No in-process state.** The sweep holds no lock and no registry, because the
  only thing that must be shared between two concurrent recoveries is the
  database row itself. A process that is killed mid-sweep leaves the rows for
  the next one rather than stranding them.
"""

import uuid
from typing import List, Optional

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import AsyncSessionLocal
from app.core.logging_config import get_logger
from app.models.document import Document
from app.models.document_chunk import DocumentChunk

logger = get_logger("app.documents")

PROCESSING = "PROCESSING"
FAILED = "FAILED"

# One message for both ways an interrupted run can be noticed -- this sweep, or
# the processing task itself being cancelled during a graceful shutdown. Sharing
# the text means the resulting state does not depend on which one ran first, so
# repeated recovery is a true no-op.
INTERRUPTED_PROCESSING_ERROR = (
    "Processing was interrupted before it finished and nothing is still "
    "processing this document, so it could not complete. This usually means the "
    "server was restarted, redeployed, or ran out of memory while the document "
    "was being indexed. It can be reprocessed."
)


async def recover_orphaned_documents(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> List[uuid.UUID]:
    """Moves every ``PROCESSING`` document to ``FAILED``; returns the ids moved.

    Called once from the lifespan, before the app starts serving, so at that
    moment no ingestion task can be in flight in *this* process: every matching
    row belongs to a run that died with a previous one.

    Not scoped by user. An interrupted document belongs to whoever uploaded it,
    and the message it is given is user-facing -- it must not contain the
    internal detail below it.
    """
    factory = session_factory or AsyncSessionLocal

    async with factory() as session:
        result = await session.execute(
            update(Document)
            .where(Document.status == PROCESSING)
            .values(status=FAILED, error_message=INTERRUPTED_PROCESSING_ERROR)
            .returning(Document.id)
        )
        recovered: List[uuid.UUID] = list(result.scalars().all())

        if recovered:
            # Chunks are normally written in the same transaction that marks the
            # document READY, so an interrupted run leaves none. Dropping any
            # that are there anyway means a retry starts from a clean slate
            # rather than re-indexing on top of superseded embeddings.
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id.in_(recovered))
            )

        await session.commit()

    if recovered:
        logger.warning(
            "orphaned_documents_recovered",
            extra={
                "recovered": len(recovered),
                "document_ids": [str(document_id) for document_id in recovered],
            },
        )
    else:
        logger.info("no_orphaned_documents", extra={"recovered": 0})

    return recovered