import json
import math
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.embeddings import embedding_service


def _as_float_list(value) -> List[float]:
    """Normalizes an embedding value from any supported storage into a float list."""
    if value is None:
        return []

    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [float(v) for v in parsed]
        except (ValueError, TypeError):
            pass

    if isinstance(value, bytes):
        for decoder in (lambda b: json.loads(b.decode("utf-8")),):
            try:
                parsed = decoder(value)
                if isinstance(parsed, list):
                    return [float(v) for v in parsed]
            except (ValueError, TypeError, UnicodeDecodeError):
                pass

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            nested = tolist()
            return [float(v) for v in nested]
        except (ValueError, TypeError):
            pass

    return []


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Cosine similarity between two equal-length float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _to_match(chunk: DocumentChunk, document_title: str, score: float) -> dict:
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "document_title": document_title,
        "chunk_index": chunk.chunk_index,
        "page_number": chunk.page_number,
        "content": chunk.content,
        "score": score,
    }


async def _retrieve_with_pgvector(
    session: AsyncSession,
    query_vector: List[float],
    subject_id,
    user_id,
    top_k: int,
) -> List[dict]:
    """DB-side kNN retrieval using the pgvector cosine distance operator (<=>)."""
    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(DocumentChunk, Document.title, distance.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.subject_id == subject_id,
            DocumentChunk.user_id == user_id,
            DocumentChunk.embedding.isnot(None),
        )
        .order_by(distance.asc())
        .limit(top_k)
    )
    result = await session.execute(stmt)

    matches = []
    for chunk, document_title, dist in result.all():
        score = max(0.0, round(1.0 - float(dist), 4))
        matches.append(_to_match(chunk, document_title, score))
    return matches


async def _retrieve_with_python(
    session: AsyncSession,
    query_vector: List[float],
    subject_id,
    user_id,
    top_k: int,
) -> List[dict]:
    """Portable in-Python retrieval used for SQLite-backed tests and fallback."""
    stmt = (
        select(DocumentChunk, Document.title)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.subject_id == subject_id,
            DocumentChunk.user_id == user_id,
        )
    )
    result = await session.execute(stmt)

    scored = []
    for chunk, document_title in result.all():
        embedding = _as_float_list(chunk.embedding)
        similarity = _cosine_similarity(query_vector, embedding)
        scored.append((similarity, chunk, document_title))

    scored.sort(key=lambda item: item[0], reverse=True)

    matches = []
    for similarity, chunk, document_title in scored[:top_k]:
        score = max(0.0, round(similarity, 4))
        matches.append(_to_match(chunk, document_title, score))
    return matches


async def retrieve_relevant_chunks(
    session: AsyncSession,
    *,
    query: str,
    subject_id,
    user_id,
    top_k: int = 5,
) -> List[dict]:
    """Semantically retrieves the top-k indexed chunks for a user-owned subject.

    Retrieval is always scoped to (subject_id, user_id) so callers can never
    reach another user's documents. Postgres uses the pgvector ``<=>`` operator;
    other dialects (e.g. SQLite under test) fall back to in-Python cosine search.
    """
    if top_k < 1:
        return []

    query_vector = await embedding_service.get_embedding(query)

    dialect_name = "postgresql"
    try:
        dialect_name = str(session.bind.dialect.name).lower()
    except Exception:
        pass

    if dialect_name == "postgresql":
        return await _retrieve_with_pgvector(
            session, query_vector, subject_id, user_id, top_k
        )

    return await _retrieve_with_python(
        session, query_vector, subject_id, user_id, top_k
    )