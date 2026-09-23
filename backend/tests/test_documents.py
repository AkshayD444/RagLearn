import io
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.rag.text_splitter import RecursiveTextSplitter
from app.rag.embeddings import embedding_service
from app.rag.document_processor import process_document_file


def create_sample_pdf_bytes() -> bytes:
    """Generates valid minimal PDF bytes with extractable text."""
    pdf_content = (
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
    return pdf_content


@pytest.mark.asyncio
async def test_text_splitter():
    splitter = RecursiveTextSplitter(chunk_size=100, chunk_overlap=20)
    pages = [
        {"page_number": 1, "text": "This is page one. It contains fundamental concepts about vector databases and RAG Q&A systems."},
        {"page_number": 2, "text": "This is page two. It explains SuperMemo SM-2 spaced repetition for flashcards and quiz evaluation."}
    ]
    chunks = splitter.split_pages(pages)
    assert len(chunks) >= 2
    assert chunks[0]["page_number"] == 1
    assert "page_number" in chunks[0]
    assert "token_count" in chunks[0]
    assert "chunk_index" in chunks[0]


@pytest.mark.asyncio
async def test_embedding_service():
    text = "Machine Learning & Neural Networks"
    vector = await embedding_service.get_embedding(text)
    assert len(vector) == 768
    assert isinstance(vector[0], float)


@pytest.mark.asyncio
async def test_document_upload_and_management_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register & Login User
        reg_res = await ac.post("/api/v1/auth/register", json={
            "email": "docstudent@example.com",
            "full_name": "Doc Student",
            "password": "docpassword123"
        })
        assert reg_res.status_code == 201
        token = reg_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Create Subject
        subj_res = await ac.post("/api/v1/subjects", json={
            "title": "Machine Learning PDF Course",
            "description": "Deep Learning Notes"
        }, headers=headers)
        assert subj_res.status_code == 201
        subject_id_str = subj_res.json()["id"]

        # 3. Upload Document (PDF)
        pdf_bytes = create_sample_pdf_bytes()
        files = {
            "file": ("lecture_notes.pdf", io.BytesIO(pdf_bytes), "application/pdf")
        }
        data = {"subject_id": subject_id_str}

        upload_res = await ac.post("/api/v1/documents/upload", data=data, files=files, headers=headers)
        assert upload_res.status_code == 201
        doc_data = upload_res.json()
        assert doc_data["title"] == "lecture_notes.pdf"
        assert doc_data["status"] == "PROCESSING"
        doc_id_str = doc_data["id"]
        user_id_str = doc_data["user_id"]

        # 4. Trigger Processing Task with UUID objects
        doc_uuid = uuid.UUID(doc_id_str)
        subject_uuid = uuid.UUID(subject_id_str)
        user_uuid = uuid.UUID(user_id_str)

        # Process document
        await process_document_file(
            document_id=doc_uuid,
            file_path=doc_data.get("file_path", ""),
            subject_id=subject_uuid,
            user_id=user_uuid
        )

        # 5. Fetch Document Details
        get_res = await ac.get(f"/api/v1/documents/{doc_id_str}", headers=headers)
        assert get_res.status_code == 200
        get_doc = get_res.json()
        assert get_doc["status"] == "READY"
        assert get_doc["chunk_count"] >= 1

        # 6. List Subject Documents
        list_res = await ac.get(f"/api/v1/documents/subject/{subject_id_str}", headers=headers)
        assert list_res.status_code == 200
        docs_list = list_res.json()
        assert len(docs_list) >= 1
        assert docs_list[0]["id"] == doc_id_str

        # 7. Delete Document
        del_res = await ac.delete(f"/api/v1/documents/{doc_id_str}", headers=headers)
        assert del_res.status_code == 204
