import uuid
import os
import shutil
from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, BackgroundTasks, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.subject import Subject
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.schemas.document import DocumentRead
from app.rag.document_processor import process_document_file

router = APIRouter(prefix="/documents", tags=["Documents"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "uploads")
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB Limit


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    subject_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Uploads a PDF study document and triggers asynchronous vector chunking."""
    # 1. Validate Subject Ownership
    res = await db.execute(
        select(Subject).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    subject = res.scalar_one_or_none()
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found or access denied."
        )

    # 2. Validate File Format (.pdf)
    filename = file.filename or "uploaded_document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file extension. Only PDF (.pdf) files are supported."
        )

    # Read header bytes to validate PDF magic numbers (%PDF-)
    header = await file.read(5)
    await file.seek(0)
    if not header.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Corrupted file payload. Magic bytes check failed for PDF."
        )

    # Check file size
    file.file.seek(0, 2)
    file_size = file.file.tell()
    await file.seek(0)

    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds maximum allowed size of 20MB (received {file_size / (1024*1024):.2f}MB)."
        )

    # 3. Create Storage Location
    doc_id = uuid.uuid4()
    user_subject_dir = os.path.join(UPLOAD_DIR, str(current_user.id), str(subject_id))
    os.makedirs(user_subject_dir, exist_ok=True)
    dest_file_path = os.path.join(user_subject_dir, f"{doc_id}.pdf")

    # Save file to disk
    with open(dest_file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 4. Save Document Record in DB
    document = Document(
        id=doc_id,
        subject_id=subject_id,
        user_id=current_user.id,
        title=filename,
        file_path=dest_file_path,
        file_size_bytes=file_size,
        status="PROCESSING"
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # 5. Trigger Background Task for Extraction & Embedding
    background_tasks.add_task(
        process_document_file,
        document_id=doc_id,
        file_path=dest_file_path,
        subject_id=subject_id,
        user_id=current_user.id
    )

    return DocumentRead(
        id=document.id,
        subject_id=document.subject_id,
        user_id=document.user_id,
        title=document.title,
        file_size_bytes=document.file_size_bytes,
        status=document.status,
        error_message=document.error_message,
        chunk_count=0,
        created_at=document.created_at
    )


@router.get("/subject/{subject_id}", response_model=List[DocumentRead])
async def list_subject_documents(
    subject_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Retrieves all uploaded study documents for a subject."""
    # Check subject ownership (active subjects only)
    res = await db.execute(
        select(Subject).where(
            Subject.id == subject_id,
            Subject.user_id == current_user.id,
            Subject.deleted_at.is_(None),
        )
    )
    if not res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subject not found."
        )

    # Fetch documents with chunk count
    stmt = (
        select(Document, func.count(DocumentChunk.id).label("chunk_count"))
        .outerjoin(DocumentChunk, Document.id == DocumentChunk.document_id)
        .where(Document.subject_id == subject_id, Document.user_id == current_user.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    results = await db.execute(stmt)
    
    output = []
    for doc, chunk_count in results.all():
        output.append(
            DocumentRead(
                id=doc.id,
                subject_id=doc.subject_id,
                user_id=doc.user_id,
                title=doc.title,
                file_size_bytes=doc.file_size_bytes,
                status=doc.status,
                error_message=doc.error_message,
                chunk_count=chunk_count,
                created_at=doc.created_at
            )
        )
    return output


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Gets single document processing status."""
    stmt = (
        select(Document, func.count(DocumentChunk.id).label("chunk_count"))
        .outerjoin(DocumentChunk, Document.id == DocumentChunk.document_id)
        .where(Document.id == document_id, Document.user_id == current_user.id)
        .group_by(Document.id)
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found."
        )

    doc, chunk_count = row
    return DocumentRead(
        id=doc.id,
        subject_id=doc.subject_id,
        user_id=doc.user_id,
        title=doc.title,
        file_size_bytes=doc.file_size_bytes,
        status=doc.status,
        error_message=doc.error_message,
        chunk_count=chunk_count,
        created_at=doc.created_at
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Deletes a document, its vector chunks, and underlying PDF file."""
    res = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == current_user.id
        )
    )
    doc = res.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found."
        )

    # Remove physical file if exists
    if doc.file_path and os.path.exists(doc.file_path):
        try:
            os.remove(doc.file_path)
        except OSError:
            pass

    await db.delete(doc)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
