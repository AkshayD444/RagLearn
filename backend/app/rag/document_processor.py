import uuid
import os
from pypdf import PdfReader
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.text_splitter import RecursiveTextSplitter
from app.rag.embeddings import embedding_service


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
            chunk_contents = [c["content"] for c in chunks]
            vectors = await embedding_service.get_embeddings_batch(chunk_contents)

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

        except Exception as e:
            await session.rollback()
            # Fetch doc again to record failure state
            res = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            failed_doc = res.scalar_one_or_none()
            if failed_doc:
                failed_doc.status = "FAILED"
                failed_doc.error_message = str(e)
                await session.commit()
