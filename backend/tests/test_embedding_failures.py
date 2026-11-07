"""B1 regression coverage: embeddings fail closed.

The defect this pins down is an *invisible* one. Embedding failures used to fall
back to a deterministic SHA-256 pseudo-vector, which meant a document with no
working Gemini key still reached ``status="READY"`` with meaningless vectors
stored in ``document_chunks.embedding``. Retrieval then ranked chunks by cosine
similarity between two hashes, and every quiz, flashcard and tutor answer built
on it was wrong -- while the status field, the API response and the logs all
said success.

So the assertions here are mostly about what does *not* happen: no vector is
fabricated, no document reaches READY, and no key material appears in an error
that could reach a client or a log aggregator.

The deterministic vector still exists, but only behind an explicit opt-in
(``EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK``) that the offline suite sets in
tests/conftest.py. The last section covers that path so it stays available
without drifting back into being a fallback.
"""

import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.document_processor import process_document_file
from app.rag.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingServiceError,
    GeminiEmbeddingService,
    is_placeholder_api_key,
)
from tests.conftest import TestAsyncSessionLocal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _offline_service(api_key: str = "", *, allow_fallback: bool = False):
    """A service in the production default state: fail closed, no key.

    Built with ``allow_fallback=False`` explicitly rather than relying on the
    suite-wide opt-in, because the suite turns that on globally and these tests
    are about the configuration a real deployment gets.
    """
    service = GeminiEmbeddingService()
    service.api_key_configured = not is_placeholder_api_key(api_key)
    service.allow_deterministic_fallback = allow_fallback
    return service


def _response_with(values):
    """Mimics the single-embedding response shape returned by the genai client."""
    return SimpleNamespace(embedding=SimpleNamespace(values=values))


class _LeakyTransportError(RuntimeError):
    """Stands in for a genai error, which echoes the request URL.

    The real client puts the full endpoint -- including its ``key`` query
    parameter -- into the exception text. Reproducing that here is the only way
    to prove the service strips it rather than trusting itself not to.
    """


def _text_pdf_bytes() -> bytes:
    """A minimal but valid PDF that yields one extractable text page."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 50 >> stream\n"
        b"BT /F1 12 Tf 100 700 Td (Embedding Failure Material) Tj ET\n"
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


async def _store_document(subject_id: str, user_id: str, path: str) -> uuid.UUID:
    """Inserts a PROCESSING document row and returns its id."""
    document_id = uuid.uuid4()
    async with TestAsyncSessionLocal() as session:
        session.add(
            Document(
                id=document_id,
                subject_id=uuid.UUID(subject_id),
                user_id=uuid.UUID(user_id),
                title="Embedding Failure.pdf",
                file_path=path,
                file_size_bytes=os.path.getsize(path),
                status="PROCESSING",
            )
        )
        await session.commit()
    return document_id


# ---------------------------------------------------------------------------
# Configuration failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api_key",
    ["", "   ", None, "your_gemini_api_key_here", "  your_gemini_api_key_here  "],
)
def test_placeholder_and_blank_keys_are_treated_as_unconfigured(api_key):
    assert is_placeholder_api_key(api_key) is True


def test_a_real_looking_key_is_treated_as_configured():
    assert is_placeholder_api_key("AIzaSy-not-a-placeholder") is False


@pytest.mark.asyncio
async def test_missing_api_key_fails_closed():
    """No key must raise, not fabricate a vector."""
    service = _offline_service(api_key="")

    with pytest.raises(EmbeddingConfigurationError) as excinfo:
        await service.get_embedding("some study material")

    message = str(excinfo.value)
    # Actionable: it names the setting the operator has to fix.
    assert "GEMINI_API_KEY" in message
    assert "EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK" in message


@pytest.mark.asyncio
async def test_placeholder_api_key_fails_closed(monkeypatch):
    """The .env.example value is a missing key wearing a disguise."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "your_gemini_api_key_here")
    service = GeminiEmbeddingService()
    service.allow_deterministic_fallback = False

    with pytest.raises(EmbeddingConfigurationError):
        await service.get_embedding("some study material")


@pytest.mark.asyncio
async def test_batch_fails_closed_when_no_key_is_configured():
    """The batch path must not be more forgiving than the single path."""
    service = _offline_service(api_key="")

    with pytest.raises(EmbeddingConfigurationError):
        await service.get_embeddings_batch(["first chunk", "second chunk"])


def test_deterministic_fallback_defaults_to_off_in_settings():
    """The production default is fail-closed. The suite opts in explicitly."""
    assert isinstance(settings.EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK, bool)
    # conftest sets it to True for the offline suite; what matters is that the
    # shipped default is False, so this asserts on the dataclass field default
    # rather than the value the test session overrode.
    assert (
        type(settings).model_fields["EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK"].default
        is False
    )


# ---------------------------------------------------------------------------
# API failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_failure_fails_closed(monkeypatch):
    service = _offline_service(api_key="a-real-looking-key")

    async def _boom(_text):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(service, "_call_gemini", _boom)

    with pytest.raises(EmbeddingServiceError) as excinfo:
        await service.get_embedding("some study material")

    assert "RuntimeError" in str(excinfo.value)


@pytest.mark.asyncio
async def test_transport_failure_does_not_disclose_the_api_key(monkeypatch):
    """B1: no secret value in an exception message.

    The genai client builds its errors from the request URL, which carries the
    key as a query parameter. Interpolating ``str(exc)`` would therefore publish
    the key into ``document.error_message``, API responses and the log stream.
    """
    secret = "AIzaSySUPERSECRET-value-that-must-never-appear"
    service = _offline_service(api_key=secret)

    async def _boom(_text):
        raise _LeakyTransportError(
            f"400 API key not valid. Please pass a valid API key. "
            f"request: https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-embedding-2:embedContent?key={secret}"
        )

    monkeypatch.setattr(service, "_call_gemini", _boom)

    with pytest.raises(EmbeddingServiceError) as excinfo:
        await service.get_embedding("some study material")

    message = str(excinfo.value)
    assert secret not in message
    # Not a substring either -- a truncated key would be just as bad.
    assert secret[:12] not in message
    assert "generativelanguage.googleapis.com" not in message


@pytest.mark.asyncio
async def test_response_without_a_vector_fails_closed(monkeypatch):
    """A 200 with no usable vector is a failure, not an empty result."""
    service = _offline_service(api_key="a-real-looking-key")

    async def _empty(_text):
        return SimpleNamespace(embedding=SimpleNamespace(values=[]))

    monkeypatch.setattr(service, "_call_gemini", _empty)

    with pytest.raises(EmbeddingServiceError):
        await service.get_embedding("some study material")


@pytest.mark.asyncio
async def test_response_with_no_embedding_attribute_fails_closed(monkeypatch):
    service = _offline_service(api_key="a-real-looking-key")

    async def _nothing(_text):
        return SimpleNamespace(unexpected="shape")

    monkeypatch.setattr(service, "_call_gemini", _nothing)

    with pytest.raises(EmbeddingServiceError):
        await service.get_embedding("some study material")


@pytest.mark.asyncio
async def test_wrong_dimension_response_fails_closed(monkeypatch):
    """A width mismatch is caught here, not as an opaque error on the INSERT.

    ``document_chunks.embedding`` is a fixed-width vector column, so storing the
    wrong number of components fails deep inside the insert -- long after the
    document was marked PROCESSING and with a message that says nothing about
    the model being the cause.
    """
    service = _offline_service(api_key="a-real-looking-key")

    async def _wrong_width(_text):
        return _response_with([0.1] * (service.dimension + 17))

    monkeypatch.setattr(service, "_call_gemini", _wrong_width)

    with pytest.raises(EmbeddingServiceError) as excinfo:
        await service.get_embedding("some study material")

    message = str(excinfo.value)
    assert str(service.dimension) in message
    assert str(service.dimension + 17) in message


@pytest.mark.asyncio
async def test_request_asks_gemini_for_the_column_width(monkeypatch):
    """`output_dimensionality` must actually reach the API request.

    gemini-embedding-2 defaults to 3072 dimensions. Without this parameter every
    response is 3072-wide, the width check rejects all of them, and PDF indexing
    fails with EmbeddingServiceError -- the same visible symptom as the retired

    model, so a regression here looks identical to a config problem. Pinned by
    inspecting the recorded call rather than by a live request.
    """
    service = _offline_service(api_key="a-real-looking-key")
    captured = {}

    async def _fake_embed_content(**kwargs):
        captured.update(kwargs)

    def _fake_client(api_key):
        return SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    embed_content=_fake_embed_content
                )
            )
        )

    genai_stub = SimpleNamespace(Client=_fake_client)
    types_stub = SimpleNamespace(
        EmbedContentConfig=lambda **kwargs: SimpleNamespace(**kwargs)
    )

    import sys
    from types import ModuleType

    google_pkg = ModuleType("google")
    google_pkg.__path__ = []
    genai_mod = ModuleType("google.genai")
    genai_mod.Client = _fake_client
    types_mod = ModuleType("google.genai.types")
    types_mod.EmbedContentConfig = types_stub.EmbedContentConfig
    genai_mod.types = types_mod
    google_pkg.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)

    await service._call_gemini("some study material")

    assert captured["model"] == service.model_name
    assert captured["contents"] == "some study material"
    assert captured["config"].output_dimensionality == service.dimension

@pytest.mark.asyncio
async def test_a_good_response_is_still_returned(monkeypatch):
    """The fix must not break the success path."""
    service = _offline_service(api_key="a-real-looking-key")
    expected = [0.25] * service.dimension

    async def _ok(_text):
        return _response_with(expected)

    monkeypatch.setattr(service, "_call_gemini", _ok)

    assert await service.get_embedding("some study material") == expected


@pytest.mark.asyncio
async def test_batched_response_shape_is_accepted(monkeypatch):
    """The client also returns an ``embeddings`` list; both shapes must work."""
    service = _offline_service(api_key="a-real-looking-key")
    expected = [0.5] * service.dimension

    async def _batched(_text):
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=expected)], embedding=None
        )

    monkeypatch.setattr(service, "_call_gemini", _batched)

    assert await service.get_embedding("some study material") == expected


# ---------------------------------------------------------------------------
# Batch semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_returns_nothing_when_one_item_fails(monkeypatch):
    """All-or-nothing: a short list would misalign with the caller's chunks.

    ``process_document_file`` zips vectors against chunks, so a partial batch
    would leave the remaining chunks stored against the wrong vector -- the same
    invisible corruption this whole change exists to stop.
    """
    service = _offline_service(api_key="a-real-looking-key")
    calls = {"n": 0}

    async def _sometimes_ok(text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("upstream hiccup")
        return _response_with([0.1] * service.dimension)

    monkeypatch.setattr(service, "_call_gemini", _sometimes_ok)

    with pytest.raises(EmbeddingServiceError):
        await service.get_embeddings_batch(["a", "b", "c"])


@pytest.mark.asyncio
async def test_empty_batch_is_still_an_empty_list():
    """Preserved: an empty input is not a failure."""
    service = _offline_service(api_key="a-real-looking-key")
    assert await service.get_embeddings_batch([]) == []


# ---------------------------------------------------------------------------
# Document processing must not reach READY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_fails_instead_of_becoming_ready_when_no_key_is_configured(
    registered_user, subject, monkeypatch
):
    """The end-to-end guarantee behind B1.

    A PDF with perfectly good text, an owner, and a configured subject still must
    not reach READY when embeddings cannot be produced.
    """
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    with handle:
        handle.write(_text_pdf_bytes())

    monkeypatch.setattr(
        "app.rag.document_processor.embedding_service",
        _offline_service(api_key="", allow_fallback=False),
    )

    try:
        document_id = await _store_document(
            subject, registered_user["user_id"], handle.name
        )

        # Must not raise.
        await process_document_file(
            document_id=document_id,
            file_path=handle.name,
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
        )

        async with TestAsyncSessionLocal() as session:
            document = await session.get(Document, document_id)
            assert document.status == "FAILED"
            assert document.status != "READY"
            assert document.error_message
            # The reason has to name the cause, or the owner cannot act on it.
            assert "GEMINI_API_KEY" in document.error_message
    finally:
        os.remove(handle.name)


@pytest.mark.asyncio
async def test_document_fails_instead_of_becoming_ready_when_the_api_fails(
    registered_user, subject, monkeypatch
):
    """Same guarantee for a configured key whose request fails."""
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    with handle:
        handle.write(_text_pdf_bytes())

    service = _offline_service(api_key="a-real-looking-key")

    async def _boom(_text):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(service, "_call_gemini", _boom)
    monkeypatch.setattr(
        "app.rag.document_processor.embedding_service", service
    )

    try:
        document_id = await _store_document(
            subject, registered_user["user_id"], handle.name
        )

        await process_document_file(
            document_id=document_id,
            file_path=handle.name,
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
        )

        async with TestAsyncSessionLocal() as session:
            document = await session.get(Document, document_id)
            assert document.status == "FAILED"
            assert document.error_message
            assert "RuntimeError" in document.error_message
    finally:
        os.remove(handle.name)


@pytest.mark.asyncio
async def test_failed_embedding_stores_no_chunks(
    registered_user, subject, monkeypatch
):
    """FAILED means nothing was indexed: a partial index would still be queried."""
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    with handle:
        handle.write(_text_pdf_bytes())

    monkeypatch.setattr(
        "app.rag.document_processor.embedding_service",
        _offline_service(api_key="", allow_fallback=False),
    )

    try:
        document_id = await _store_document(
            subject, registered_user["user_id"], handle.name
        )

        await process_document_file(
            document_id=document_id,
            file_path=handle.name,
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
        )

        async with TestAsyncSessionLocal() as session:
            count = await session.scalar(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_id == document_id
                )
            )
            assert count == 0
    finally:
        os.remove(handle.name)


# ---------------------------------------------------------------------------
# API boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_endpoint_returns_503_when_embeddings_fail(
    client, registered_user, subject, monkeypatch
):
    """A dependency outage is a 503, not a 500.

    The endpoints that embed the user's query are retrieval, quiz generation,
    flashcard generation and the tutor. Letting the error escape produced a 500,
    which reads as a bug in the endpoint rather than Gemini being down, and it
    did so on every request for the duration of the outage.
    """
    monkeypatch.setattr(
        "app.rag.retriever.embedding_service",
        _offline_service(api_key="", allow_fallback=False),
    )

    res = await client.post(
        "/api/v1/rag/ask",
        json={"subject_id": subject, "question": "What does this cover?"},
        headers=registered_user["headers"],
    )

    assert res.status_code == 503, res.text
    # The client learns that the service is unavailable, not how it is configured.
    assert "GEMINI_API_KEY" not in res.text
    assert "embedding" in res.text.lower()


@pytest.mark.asyncio
async def test_retrieval_endpoint_still_works_in_offline_mode(
    client, registered_user, subject, seeded_chunks
):
    """Guard against the fix over-reaching: the happy path is untouched."""
    await seeded_chunks(["Vector databases store embeddings for semantic search."])

    res = await client.post(
        "/api/v1/rag/ask",
        json={"subject_id": subject, "question": "What stores embeddings?"},
        headers=registered_user["headers"],
    )

    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# The explicit opt-in path still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_vectors_work_when_explicitly_enabled():
    """The test/dev affordance is preserved -- it is just opt-in now."""
    service = _offline_service(api_key="", allow_fallback=True)

    vector = await service.get_embedding("deterministic input")

    assert len(vector) == service.dimension == 768
    assert vector == service._generate_fallback_vector("deterministic input")


@pytest.mark.asyncio
async def test_deterministic_vectors_are_still_reproducible():
    service = _offline_service(api_key="", allow_fallback=True)

    first = await service.get_embedding("same text")
    second = await service.get_embedding("same text")

    assert first == second
    assert first != await service.get_embedding("different text")


@pytest.mark.asyncio
async def test_deterministic_batch_still_works_when_explicitly_enabled():
    service = _offline_service(api_key="", allow_fallback=True)

    texts = ["one", "two", "three"]
    batch = await service.get_embeddings_batch(texts)

    assert len(batch) == 3
    assert batch == [await service.get_embedding(text) for text in texts]


@pytest.mark.asyncio
async def test_every_embedding_failure_shares_one_base_exception():
    """Callers can catch one type regardless of which guard fired."""
    service = _offline_service(api_key="")

    with pytest.raises(EmbeddingError):
        await service.get_embedding("anything")

    assert issubclass(EmbeddingConfigurationError, EmbeddingError)
    assert issubclass(EmbeddingServiceError, EmbeddingError)


@pytest.mark.asyncio
async def test_document_processing_still_succeeds_in_explicit_offline_mode(
    registered_user, subject, monkeypatch
):
    """The offline path is a working feature, not a degraded stub."""
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    with handle:
        handle.write(_text_pdf_bytes())

    monkeypatch.setattr(
        "app.rag.document_processor.embedding_service",
        _offline_service(api_key="", allow_fallback=True),
    )

    try:
        document_id = await _store_document(
            subject, registered_user["user_id"], handle.name
        )

        await process_document_file(
            document_id=document_id,
            file_path=handle.name,
            subject_id=uuid.UUID(subject),
            user_id=uuid.UUID(registered_user["user_id"]),
        )

        async with TestAsyncSessionLocal() as session:
            document = await session.get(Document, document_id)
            assert document.status == "READY"
            assert document.error_message is None
            count = await session.scalar(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_id == document_id
                )
            )
            assert count > 0
    finally:
        os.remove(handle.name)


def test_no_placeholder_or_secret_is_hardcoded_in_the_module():
    """The service must not carry a usable key of its own."""
    source = Path(__file__).resolve().parents[1] / "app" / "rag" / "embeddings.py"
    text = source.read_text(encoding="utf-8")
    # The placeholder constant is a *comparison* target and is expected; what
    # must not appear is anything shaped like a real Gemini key.
    assert "AIza" not in text
