import re
from typing import Dict, List, Optional

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
    "from its own back. "
    "Vary the form of the fronts across the set: ask about definitions, purposes, "
    "requirements, sequences and comparisons. Do not build every front by removing a "
    "single word from the same sentence, and do not reuse one sentence for several cards. "
    "Running headers, page numbers, module labels and footers are document layout, not "
    "study material, so never make them the subject of a card. "
    "Each card must cover a distinct concept from the excerpts."
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


# Verbs that signal a definitional study sentence, grouped by the question shape
# each one supports. Only used to recognise a sentence that is worth asking
# about; no external knowledge is introduced.
_ROLE_VERBS = frozenset(
    """allow require provide use involve perform support enable mean represent
    define describe specify determine control manage handle contain include
    replace avoid reduce increase maintain ensure guarantee distinguish
    store hold record refer appear denote indicate mark name call cause need
    ground combine separate coordinate""".split()
)
_PROCESS_VERBS = frozenset(
    """proceed work occur happen operate function begin start end follow""".split()
)


def _is_definitional_verb(word: str) -> Optional[str]:
    """Classify a word as a definitional verb, tolerating inflection.

    Returns "role", "process", or None. Without inflection handling the scan
    missed "allows" and "requires" -- the commonest verbs in lecture prose --
    and fell through to the cloze path for most sentences.
    """
    lowered = word.strip(",;:.").lower()
    if lowered in _ROLE_VERBS:
        return "role"
    if lowered in _PROCESS_VERBS:
        return "process"

    # Third person / past / gerund forms of the same stems.
    for suffix in ("s", "es", "ed", "ing"):
        if lowered.endswith(suffix):
            stem = lowered[: -len(suffix)]
            if stem in _ROLE_VERBS:
                return "role"
            if stem in _PROCESS_VERBS:
                return "process"
    # Irregular forms worth covering explicitly.
    if lowered in {"begins", "began", "begun", "begun"}:
        return "process"
    return None


def _leading_noun_phrase(sentence: str) -> Optional[str]:
    """The sentence's subject phrase, or None when it is not definitional.

    Keeps only the opening noun phrase of a sentence that has a recognisable
    verb, so the question can name the thing actually being described. This is
    pattern recognition over the user's own text, not generation.

    The verb must be *immediately* after the subject. Scanning the whole
    sentence for the first known verb instead would happily swallow half a
    sentence into the subject ("CPU performs one instruction to"), producing a
    nonsense question.
    """
    words = sentence.split()
    if len(words) < 5:
        return None

    subject_words = []
    verb_kind = None
    for word in words:
        verb_kind = _is_definitional_verb(word)
        if verb_kind is not None:
            break
        subject_words.append(word)
        if len(subject_words) > 6:
            return None
    else:
        return None

    # Drop a leading article; keep the rest as-is so acronyms and proper nouns
    # ("Direct Memory Access", "DMA controller") survive verbatim.
    if subject_words and subject_words[0].lower() in {"a", "an", "the"}:
        subject_words = subject_words[1:]
    if not subject_words:
        return None

    subject = " ".join(subject_words).strip(" ,;:-")
    if len(subject) < 3:
        return None
    # Reject a "subject" that is mostly punctuation or digits, e.g. a bare label.
    if not re.search(r"[A-Za-z]{3}", subject):
        return None
    # The subject must be a phrase, not a clause: a comma means the verb we
    # matched was not this sentence's main verb.
    if "," in subject:
        return None
    return subject


def _question_for(subject: str, sentence: str, rotation: int) -> str:
    """One question about ``subject``, chosen to keep fronts varied.

    A sequence sentence ("A DMA transfer proceeds by...") gets the process form
    rather than a static-definition one, because that is what the sentence
    actually supports. The remaining forms rotate so a deck does not repeat a
    single template.
    """
    is_process = any(
        _is_definitional_verb(word) == "process" for word in sentence.split()
    )
    if is_process:
        # One card per concept, so there is nothing to rotate against: a
        # sequence sentence always gets the sequence question.
        return f"How does {subject} work?"
    forms = [
        f"What is {subject}?",
        f"What does {subject} do?",
        f"What is the role of {subject} in this system?",
    ]
    return forms[rotation % len(forms)]


def _generate_deterministic(card_count: int, chunks: List[dict]) -> List[dict]:
    """Offline fallback: builds concept-based flashcards from retrieved sentences.

    One card per *concept*, not per blankable word. The previous implementation
    emitted one card for every removable word in a sentence, so a 10-card deck
    consumed 10 blanks from the first sentence and never reached the later ones
    -- ten near-identical cloze cards from a single line of text.

    Each card pairs a question about a concept with the source sentence as the
    answer, so the deck stays strictly grounded in the student's own material.
    Sentences are consumed in order and each is used once, which is what spreads
    the deck across distinct concepts.
    """
    sentences = _extract_sentences(chunks)
    if not sentences:
        return []

    # Group by concept so a subject repeated across sentences yields one card
    # with the fullest explanation, rather than several near-duplicates.
    by_concept: Dict[str, List[tuple]] = {}
    order: List[str] = []
    for sentence, chunk in sentences:
        subject = _leading_noun_phrase(sentence)
        key = (subject or sentence).lower()
        if key not in by_concept:
            by_concept[key] = []
            order.append(key)
        by_concept[key].append((subject, sentence, chunk))

    items: List[dict] = []
    for key in order:
        if len(items) >= card_count:
            break

        subject, sentence, chunk = by_concept[key][0]
        if subject is None:
            # No recognisable subject: keep the old grounded cloze shape for this
            # one sentence rather than dropping usable study content.
            tokens = _candidate_tokens(sentence)
            if not tokens:
                continue
            token = tokens[0]
            blanked, replaced = re.subn(
                re.escape(token), "_____", sentence, count=1, flags=re.IGNORECASE
            )
            if not replaced:
                continue
            items.append(
                _card_from_chunk(
                    f'Complete the statement: "{blanked}"',
                    f'"{token}" - {sentence}',
                    _source_hint(chunk),
                    chunk,
                )
            )
            continue

        front = _question_for(subject, sentence, len(items))
        items.append(
            _card_from_chunk(
                front,
                sentence,
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
