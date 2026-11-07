"""Text embeddings for the RAG pipeline.

Why this fails closed
---------------------
An embedding is either a real Gemini vector or the call raises. There is no
third option, and that is a deliberate reversal of an earlier design that fell
back to a hash-derived vector whenever the API was unreachable.

A wrong vector is worse than a missing one. A chunk stored with SHA-256 noise is
marked ``READY``, the retriever then ranks candidates by cosine similarity
between two meaningless numbers, and every quiz, flashcard and tutor answer
derived from it is quietly wrong -- while the document status, the API response
and the log stream all report success. The failure mode is invisible at exactly
the point where somebody would think to look.

So the deterministic vector is now opt-in through
``EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK`` (see app/core/config.py), which the
offline test suite enables and which defaults to false everywhere else. Every
other path raises: no key, the documented placeholder key, a transport error, a
rejected key, a response with no vector in it, and a vector whose width does not
match ``EMBEDDING_DIMENSIONS``.

Failures propagate to the callers, which is what makes them actionable:

* :mod:`app.rag.document_processor` catches the error and records
  ``status="FAILED"`` with the reason, so the document never reaches READY and
  the owner can retry it once the key works;
* the retrieval-backed endpoints surface HTTP 503 via the handler in
  app/main.py, instead of returning an unrelated 500.

Secrets
-------
No exception message, log line or error string contains the API key or any
substring of it. The key is only ever reported as configured or not. Gemini's own
transport exceptions are reduced to their type name, because those objects echo
the request -- including its ``key`` query parameter -- back in ``str(exc)``.
"""

import hashlib
from typing import Any, List, Optional, Sequence

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("app.rag.embeddings")

#: The value shipped in .env.example. Treated as "not configured" exactly like
#: an empty string, because that is what it is.
PLACEHOLDER_API_KEY = "your_gemini_api_key_here"

_CONFIG_ERROR = (
    "GEMINI_API_KEY is not configured, so no embedding could be generated. "
    "Set a valid key in the environment. For a fully offline development or "
    "test run, set EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK=true instead -- but "
    "be aware those vectors carry no semantic meaning and make retrieval and "
    "every answer built on it incorrect."
)


class EmbeddingError(RuntimeError):
    """No usable embedding could be produced.

    Base class for every failure below, so a caller that only wants to know
    "can this text be embedded?" catches one type.
    """


class EmbeddingConfigurationError(EmbeddingError):
    """No usable Gemini API key is configured."""


class EmbeddingServiceError(EmbeddingError):
    """Gemini was called and did not return a usable vector.

    Covers transport failures, rejected keys, empty/malformed responses and
    wrong-width vectors.
    """


def is_placeholder_api_key(api_key: Optional[str]) -> bool:
    """True when the key is absent, blank, or the documented placeholder.

    Stripped before comparison so a key that is only whitespace counts as
    missing rather than being sent to the API as an empty credential.
    """
    if not api_key:
        return True
    return api_key.strip() in {"", PLACEHOLDER_API_KEY}


class GeminiEmbeddingService:
    """Generates text embeddings using the Google Gemini API.

    The model and width come from settings rather than being hardcoded here.
    `EMBEDDING_DIMENSIONS` is a hard schema constraint -- `document_chunks
    .embedding` is Vector(768) -- so changing it requires a migration and a full
    re-embedding pass over every stored chunk. That is also why a wrong-width
    response is rejected here rather than being allowed to fail later inside the
    INSERT, where the cause would be much harder to see.
    """

    def __init__(self) -> None:
        self.model_name = settings.GEMINI_EMBEDDING_MODEL
        self.dimension = settings.EMBEDDING_DIMENSIONS
        # Resolved once, in the constructor, matching how the generative
        # services capture `api_key_configured`. The offline test suite flips
        # these two attributes on the singleton rather than reaching into
        # settings on every call.
        self.api_key_configured = not is_placeholder_api_key(settings.GEMINI_API_KEY)
        self.allow_deterministic_fallback = bool(
            settings.EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK
        )

    async def get_embedding(self, text: str) -> List[float]:
        """Returns one embedding, or raises :class:`EmbeddingError`.

        A blocked/absent key, a transport error, an empty response and a
        wrong-width response are all failures. The deterministic vector is
        returned only when the operator explicitly asked for offline mode.
        """
        if not self.api_key_configured:
            if self.allow_deterministic_fallback:
                return self._generate_fallback_vector(text)
            # The key is referenced by name only; its value never appears here.
            raise EmbeddingConfigurationError(_CONFIG_ERROR)

        try:
            response = await self._call_gemini(text)
        except EmbeddingError:
            raise
        except Exception as exc:
            # `str(exc)` is deliberately not used: the genai client includes the
            # request URL in its error text, and that URL carries the api_key as
            # a query parameter. The type name is enough to diagnose a 401 from a
            # 429 from a DNS failure alongside the correlation id.
            logger.error(
                "gemini_embedding_request_failed",
                extra={
                    "model": self.model_name,
                    "error_type": type(exc).__name__,
                },
            )
            raise EmbeddingServiceError(
                f"The Gemini embedding request failed "
                f"({type(exc).__name__}) for model {self.model_name!r}. "
                f"No embedding was produced."
            ) from exc

        return self._coerce_vector(response)

    async def get_embeddings_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Embeds several texts, with exactly the failure semantics of one call.

        One failure fails the whole batch. A partial list is never returned: the
        caller zips the result against its chunks, so a short list would silently
        leave the remaining chunks either unstored or stored against the wrong
        vector, which is the same class of invisible corruption this module
        exists to prevent.
        """
        items = list(texts)
        if not items:
            return []
        return [await self.get_embedding(text) for text in items]

    async def _call_gemini(self, text: str) -> Any:
        """Invokes the embedding API. Raises on transport failure.

        `output_dimensionality` is required, not an optimisation. gemini-embedding-2
        emits 3072 dimensions by default, so without this the response would be
        rejected below by the width check -- correctly, since the column is
        Vector(768). The model is trained with Matryoshka Representation
        Learning, so asking for `self.dimension` truncates to a real 768-dim
        vector rather than padding or approximating one.
        """
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return await client.aio.models.embed_content(
            model=self.model_name,
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=self.dimension,
            ),
        )

    def _coerce_vector(self, response: Any) -> List[float]:
        """Extracts and validates the vector from an embedding response.

        Handles both response shapes the API uses: a single ``embedding`` and a
        batched ``embeddings`` list. Anything else -- no attribute, empty values,
        a wrong number of dimensions -- is a failure rather than a guess.
        """
        values = None
        single = getattr(response, "embedding", None)
        if single is not None and getattr(single, "values", None):
            values = single.values
        else:
            batched = getattr(response, "embeddings", None)
            if batched:
                first = batched[0]
                values = getattr(first, "values", None)

        if not values:
            raise EmbeddingServiceError(
                f"Gemini returned no embedding values for model "
                f"{self.model_name!r}. No vector was produced."
            )

        try:
            vector = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise EmbeddingServiceError(
                f"Gemini returned a non-numeric embedding for model "
                f"{self.model_name!r}."
            ) from exc

        if len(vector) != self.dimension:
            # Left to the database this surfaces as an opaque dimension error on
            # the INSERT, long after the document has been marked PROCESSING.
            raise EmbeddingServiceError(
                f"Gemini returned a {len(vector)}-dimensional embedding but "
                f"EMBEDDING_DIMENSIONS is {self.dimension}. "
                f"document_chunks.embedding is Vector({self.dimension}), so this "
                f"vector cannot be stored. Either the model changed or "
                f"EMBEDDING_DIMENSIONS does not match it."
            )
        return vector

    def _generate_fallback_vector(self, text: str) -> List[float]:
        """Deterministic pseudo-vector, for explicitly offline runs only.

        Reachable only when EMBEDDINGS_ALLOW_DETERMINISTIC_FALLBACK is true.
        It is derived from the text so the same text always yields the same
        vector -- which is what makes the offline test suite reproducible -- but
        it encodes no meaning, and two texts that mean the same thing are as
        unrelated here as two that do not. Semantic retrieval over these vectors
        is not merely degraded, it is arbitrary.
        """
        hash_digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector = []
        for i in range(self.dimension):
            byte_val = hash_digest[i % len(hash_digest)]
            val = (float(byte_val) / 255.0) - 0.5
            vector.append(round(val, 6))
        return vector


embedding_service = GeminiEmbeddingService()
