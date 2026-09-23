import uuid
from typing import List
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RagScopedRequest(BaseModel):
    """Shared request payload scoped to a single user-owned subject."""
    subject_id: uuid.UUID


class RetrievalRequest(RagScopedRequest):
    """Semantic retrieval request against a subject's indexed chunks."""
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Query must not be blank.")
        return value


class RetrievedChunk(BaseModel):
    """A single semantic search hit with source/citation metadata."""
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    chunk_index: int
    page_number: int
    content: str
    score: float

    model_config = ConfigDict(from_attributes=True)


class RetrievalResponse(BaseModel):
    """Result of a semantic retrieval query."""
    subject_id: uuid.UUID
    query: str
    results: List[RetrievedChunk] = Field(default_factory=list)


class RagQuestion(RagScopedRequest):
    """Grounded Q&A request for the RAG tutor."""
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question must not be blank.")
        return value


class RagSource(RetrievedChunk):
    """Citation metadata attached to a grounded AI answer."""
    pass


class RagAnswerResponse(BaseModel):
    """Grounded answer with supporting source citations."""
    subject_id: uuid.UUID
    question: str
    answer: str
    sources: List[RagSource] = Field(default_factory=list)
    model_used: str