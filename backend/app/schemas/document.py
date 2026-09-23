import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class DocumentChunkRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    page_number: int
    token_count: int

    model_config = ConfigDict(from_attributes=True)


class DocumentRead(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    file_size_bytes: int
    status: str
    error_message: Optional[str] = None
    chunk_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
