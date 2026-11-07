import asyncio
import logging
from typing import Dict, List

from app.core.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an AI study tutor for a university student. "
    "Answer the student's question using ONLY the provided study-material excerpts. "
    "The excerpts are labeled [Source N]. Support claims with inline citations such as [1] or [2]. "
    "If the excerpts do not contain the answer, say so clearly and suggest what material is missing. "
    "Do not use outside knowledge or speculate."
)

# Total attempts, not extra retries: 1 initial call + 2 retries.
MAX_GENERATION_ATTEMPTS = 3
# Exponential: 1s, then 2s. Bounded on purpose -- this runs inside a
# rate-limited request handler, so an unbounded or long backoff would hold the
# connection open and convert a transient upstream blip into a user-visible
# timeout.
RETRY_BASE_DELAY_SECONDS = 1.0
# 503 only. Deliberately narrow:
#   401/403 auth      -> retrying cannot help and risks lockout
#   404 invalid model -> permanent; retrying just delays the same 404
#   400 bad request   -> permanent
#   429 quota         -> has its own quota semantics, handled elsewhere
# A broad "5xx or timeout" rule would hide real outages behind silent retries.
RETRYABLE_STATUS_CODES = frozenset({503})
RETRYABLE_STATUS_NAMES = frozenset({"UNAVAILABLE"})


def is_transient_gemini_error(exc: BaseException) -> bool:
    """True only for transient upstream-unavailable errors worth retrying.

    Matches on the SDK's structured ``code``/``status`` rather than parsing the
    message text, so it cannot drift when Google rewords an error string.
    Anything that is not an ``APIError`` (a local bug, an import failure) is not
    retryable -- retrying those just triples the latency before the same
    failure.
    """
    try:
        from google.genai.errors import APIError
    except Exception:  # pragma: no cover - SDK always ships this
        return False

    if not isinstance(exc, APIError):
        return False

    if getattr(exc, "code", None) in RETRYABLE_STATUS_CODES:
        return True
    return str(getattr(exc, "status", "") or "").upper() in RETRYABLE_STATUS_NAMES


class RagTutorService:
    """Grounds Gemini answers in a student's own retrieved document chunks."""

    def __init__(self):
        self.model_name = settings.GEMINI_QA_MODEL
        self.max_context_chunks = 8
        self.max_context_chars = 12000
        self.api_key_configured = bool(
            settings.GEMINI_API_KEY
            and settings.GEMINI_API_KEY != "your_gemini_api_key_here"
        )

    async def answer_question(self, question: str, chunks: List[dict]) -> Dict[str, str]:
        """Returns {'answer': str, 'model_used': str} grounded in the retrieved chunks."""
        if self.api_key_configured:
            try:
                answer = await self._generate_with_gemini(question, chunks)
                return {"answer": answer, "model_used": self.model_name}
            except Exception as e:
                print(f"[RagTutorService] Gemini API warning: {e}. Using deterministic fallback.")

        return {
            "answer": self._fallback_answer(question, chunks),
            "model_used": "deterministic-fallback",
        }

    def _build_context(self, chunks: List[dict]) -> str:
        """Serializes top chunks into numbered [Source N] excerpts under a char budget."""
        snippets = []
        total_chars = 0
        for i, chunk in enumerate(chunks[: self.max_context_chunks], start=1):
            snippet = (
                f"[Source {i}] Document: \"{chunk['document_title']}\" | "
                f"Page {chunk['page_number']}\n{chunk['content']}"
            )
            total_chars += len(snippet)
            if total_chars > self.max_context_chars:
                break
            snippets.append(snippet)
        return "\n\n".join(snippets)

    async def _generate_with_gemini(self, question: str, chunks: List[dict]) -> str:
        from google import genai

        context = self._build_context(chunks)
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"STUDY MATERIAL EXCERPTS:\n{context}\n\n"
            f"STUDENT QUESTION:\n{question}\n\n"
            "Provide your grounded answer now."
        )

        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        last_error: BaseException | None = None
        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            try:
                response = await client.aio.models.generate_content(
                    model=self.model_name, contents=prompt
                )
            except Exception as exc:
                last_error = exc
                # Non-transient failures (auth, invalid model, bad request) are
                # re-raised untouched so the caller falls back immediately.
                if not is_transient_gemini_error(exc):
                    raise
                if attempt == MAX_GENERATION_ATTEMPTS:
                    break
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                # Type name only, never `str(exc)`: the genai client puts the
                # request URL -- and therefore the api_key -- in the exception
                # text, which would leak the key into the log stream.
                logger.warning(
                    "gemini_generation_unavailable",
                    extra={
                        "model": self.model_name,
                        "attempt": attempt,
                        "max_attempts": MAX_GENERATION_ATTEMPTS,
                        "delay_seconds": delay,
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(delay)
                continue

            text = getattr(response, "text", None)
            if not text:
                raise RuntimeError("Gemini returned an empty response.")
            return text.strip()

        # Exhausted every attempt: surface the last upstream error so
        # answer_question's existing handler produces the deterministic
        # fallback rather than pretending the call never happened.
        raise last_error if last_error is not None else RuntimeError(
            "Gemini generation failed without a recorded error."
        )

    def _fallback_answer(self, question: str, chunks: List[dict]) -> str:
        """Offline deterministic fallback when no Gemini API key is configured."""
        top = chunks[0]
        excerpt = top["content"].strip()
        if len(excerpt) > 500:
            excerpt = excerpt[:500].rstrip() + "..."

        sources = ", ".join(
            f"S{i}: {c['document_title']} (page {c['page_number']})"
            for i, c in enumerate(chunks[:5], start=1)
        )

        return (
            "Grounded AI response generation requires a configured GEMINI_API_KEY. "
            "The most relevant passage found in your study materials is:\n\n"
            f"\"{excerpt}\"\n\n"
            f"Relevant sources: {sources}"
        )


rag_tutor_service = RagTutorService()
