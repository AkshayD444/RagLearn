import re
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.flashcard import Flashcard, FlashcardDeck
from app.models.subject import Subject
from app.rag.retriever import retrieve_relevant_chunks
from app.services.quiz_engine import (
    NoStudyMaterialsError,
    _build_context,
    _candidate_tokens,
    _extract_sentences,
    _parse_json_array,
)

FLASHCARD_SYSTEM_PROMPT = (
    "You are a flashcard generator. "
    "Generate question/answer pairs based EXCLUSIVELY on the provided study-material excerpts. "
    "Every fact in every front and back must come from the excerpts. "
    "Do not use outside knowledge, do not invent facts, and do not create a card that "
    "cannot be answered from the excerpts alone. "
    "Each back must be concise and self-contained, and each front must be answerable "
    "from its own back."
)


def _api_key_configured() -> bool:
    """Reports whether a usable Gemini key is present.

    The key is resolved on every call rather than captured in ``__init__`` so a
    rotated or injected key takes effect without restarting the process, matching
    how ``app.rag.embeddings`` reads its configuration.
    """
    return bool(
        settings.GEMINI_API_KEY
        and settings.GEMINI_API_KEY != "your_gemini_api_key_here"
    )


def _card_from_chunk(
    front: str,
    back: str,
    hint: Optional[str],
    chunk: dict,
) -> dict:
    """Assembles a Flashcard-compatible dict with source citation metadata."""
    return {
        "front_text": front,
        "back_text": back,
        "hint": hint,
        "document_id": chunk.get("document_id"),
        "chunk_id": chunk.get("chunk_id"),
        "document_title": chunk.get("document_title"),
        "page_number": chunk.get("page_number"),
        "source_snippet": (chunk.get("content") or "")[:400],
    }


def _source_hint(chunk: dict) -> Optional[str]:
    """A grounded pointer back to the originating document and page."""
    title = chunk.get("document_title")
    page = chunk.get("page_number")
    if title and page:
        return f'From "{title}", page {page}.'
    if title:
        return f'From "{title}".'
    return None


def _generate_deterministic(card_count: int, chunks: List[dict]) -> List[dict]:
    """Offline fallback: builds grounded cloze flashcards.

    Both sides of every card are derived from the retrieved chunk text itself --
    the front is the sentence with one term blanked, the back is that term plus
    the untouched sentence -- so no content can be introduced that the user's own
    material does not contain.
    """
    sentences = _extract_sentences(chunks)

    candidates = []
    seen = set()
    for sentence, chunk in sentences:
        for token in _candidate_tokens(sentence):
            key = (sentence, token)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((token, sentence, chunk))

    items: List[dict] = []
    for token, sentence, chunk in candidates:
        if len(items) >= card_count:
            break

        blanked, _ = re.subn(
            re.escape(token), "_____", sentence, count=1, flags=re.IGNORECASE
        )
        if blanked == sentence:
            # The token was matched case-insensitively into an identical string;
            # nothing was blanked, so the card would give the answer away.
            continue

        items.append(
            _card_from_chunk(
                f'Complete the statement: "{blanked}"',
                f'"{token}" - {sentence}',
                _source_hint(chunk),
                chunk,
            )
        )
    return items


class FlashcardGenerationService:
    """Generates grounded flashcards from a user's own indexed document chunks."""

    def __init__(self):
        self.model_name = settings.GEMINI_QA_MODEL
        self.max_context_chunks = 20
        self.max_context_chars = 16000

    async def generate_deck(
        self,
        session: AsyncSession,
        *,
        subject: Subject,
        card_count: int,
        topic: Optional[str],
    ) -> FlashcardDeck:
        """Retrieves subject context, generates grounded cards, and persists a deck."""
        query = (topic or "").strip() or subject.title or "study material"
        retrieval_top_k = min(max(card_count * 2, 8), 20)

        chunks = await retrieve_relevant_chunks(
            session,
            query=query,
            subject_id=subject.id,
            user_id=subject.user_id,
            top_k=retrieval_top_k,
        )
        if not chunks:
            raise NoStudyMaterialsError()

        items: List[dict] = []
        if _api_key_configured():
            try:
                items = await self._generate_with_gemini(
                    card_count, topic, chunks
                )
            except Exception as e:
                print(
                    f"[FlashcardGenerationService] Gemini API warning: {e}. "
                    "Using deterministic fallback."
                )
        if not items:
            items = _generate_deterministic(card_count, chunks)
        if not items:
            raise NoStudyMaterialsError()

        deck = FlashcardDeck(
            user_id=subject.user_id,
            subject_id=subject.id,
            title=f"{subject.title} Flashcards",
            topic=topic,
            card_count=len(items),
        )
        session.add(deck)
        await session.flush()

        for position, item in enumerate(items):
            session.add(
                Flashcard(
                    deck_id=deck.id,
                    topic=topic,
                    position=position,
                    **item,
                )
            )

        await session.commit()
        await session.refresh(deck)
        return deck

    async def _generate_with_gemini(
        self,
        card_count: int,
        topic: Optional[str],
        chunks: List[dict],
    ) -> List[dict]:
        from google import genai

        context = _build_context(
            chunks, self.max_context_chunks, self.max_context_chars
        )
        topic_line = f"\nFocus the cards on this topic: {topic}." if topic else ""
        prompt = (
            f"{FLASHCARD_SYSTEM_PROMPT}\n\n"
            f"Number of flashcards: {card_count}\n"
            f"{topic_line}\n\n"
            f"STUDY MATERIAL EXCERPTS:\n{context}\n\n"
            "Respond with ONLY a JSON array (no markdown fences, no prose) containing "
            f"exactly {card_count} objects. Each object must have these keys:\n"
            '  "front": string (the question side),\n'
            '  "back": string (the answer side),\n'
            '  "hint": string or null (an optional short cue),\n'
            '  "source_index": integer (1-based index of the [Source N] excerpt used)\n'
        )

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(model=self.model_name, contents=prompt)
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini returned an empty response.")

        raw_items = _parse_json_array(text)
        return self._validate_gemini_items(raw_items, chunks, card_count)

    @staticmethod
    def _validate_gemini_items(
        raw_items: list,
        chunks: List[dict],
        card_count: int,
    ) -> List[dict]:
        """Validates model output and maps each card onto a real retrieved chunk."""
        items: List[dict] = []
        for entry in raw_items:
            if len(items) >= card_count:
                break
            if not isinstance(entry, dict):
                continue

            front = str(entry.get("front", "")).strip()
            back = str(entry.get("back", "")).strip()
            hint_raw = entry.get("hint")
            source_index = entry.get("source_index")

            if not front or not back:
                continue

            hint = str(hint_raw).strip() if hint_raw is not None else ""
            if not isinstance(source_index, int) or not 1 <= source_index <= len(chunks):
                source_index = 1

            chunk = chunks[source_index - 1]
            items.append(
                _card_from_chunk(front, back, hint or None, chunk)
            )
        return items


flashcard_generation_service = FlashcardGenerationService()
