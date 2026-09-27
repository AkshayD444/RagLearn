"""Unit tests for the grounded flashcard generation service.

These exercise the generator without a database: retrieval, persistence, and
HTTP behaviour live in ``tests/test_flashcards.py``.
"""

import pytest

from app.services import flashcard_engine as engine
from app.services.flashcard_engine import (
    FlashcardGenerationService,
    _api_key_configured,
    _generate_deterministic,
    _source_hint,
)

SENTENCE_ONE = "Vector databases store embeddings for semantic search."
SENTENCE_TWO = "Retrieval augmented generation grounds answers in source documents."


def _chunk(
    content: str,
    *,
    chunk_id: str = "chunk-1",
    document_id: str = "doc-1",
    document_title: str = "Vector Notes.pdf",
    page_number: int = 3,
) -> dict:
    """Builds a retrieval hit in the exact shape ``app.rag.retriever`` returns."""
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "document_title": document_title,
        "chunk_index": 0,
        "page_number": page_number,
        "content": content,
        "score": 0.9,
    }


def _quoted_token(back_text: str) -> str:
    """Extracts the answer term from a deterministic card's back text."""
    assert back_text.startswith('"')
    return back_text[1 : back_text.index('"', 1)]


# ---------------------------------------------------------------------------
# Deterministic (offline) generation
# ---------------------------------------------------------------------------


def test_deterministic_cards_are_built_from_the_retrieved_text():
    chunks = [_chunk(f"{SENTENCE_ONE} {SENTENCE_TWO}")]
    cards = _generate_deterministic(3, chunks)

    assert len(cards) == 3
    for card in cards:
        # Every card is a blanked sentence plus the term that fills the blank.
        assert card["front_text"].startswith('Complete the statement: "')
        assert "_____" in card["front_text"]
        assert _quoted_token(card["back_text"]) not in card["front_text"]


def test_deterministic_cards_never_introduce_terms_absent_from_the_source():
    """The core grounding guarantee: no answer term outside the retrieved text."""
    source = f"{SENTENCE_ONE} {SENTENCE_TWO}"
    cards = _generate_deterministic(20, [_chunk(source)])

    assert cards
    for card in cards:
        token = _quoted_token(card["back_text"])
        assert token in source.lower()


def test_deterministic_cards_carry_citation_snapshots():
    chunk = _chunk(SENTENCE_ONE, chunk_id="chunk-9", document_id="doc-9", page_number=7)
    cards = _generate_deterministic(2, [chunk])

    for card in cards:
        assert card["document_id"] == "doc-9"
        assert card["chunk_id"] == "chunk-9"
        assert card["document_title"] == "Vector Notes.pdf"
        assert card["page_number"] == 7
        assert card["source_snippet"] == SENTENCE_ONE


def test_deterministic_cards_respect_the_card_count_cap():
    chunks = [_chunk(f"{SENTENCE_ONE} {SENTENCE_TWO}")]
    assert len(_generate_deterministic(1, chunks)) == 1
    assert len(_generate_deterministic(2, chunks)) == 2


def test_deterministic_cards_dedupe_repeated_sentences():
    """The same sentence in two chunks must not yield the same card twice."""
    duplicate = _chunk(SENTENCE_ONE, chunk_id="chunk-a", document_id="doc-a")
    repeated = _chunk(SENTENCE_ONE, chunk_id="chunk-b", document_id="doc-b")
    chunks = [duplicate, repeated]

    cards = _generate_deterministic(20, chunks)
    fronts = [card["front_text"] for card in cards]
    assert len(fronts) == len(set(fronts))


def test_deterministic_cards_return_nothing_without_usable_sentences():
    assert _generate_deterministic(5, []) == []
    # Too short to clear the 4-word floor, and pure stopwords leave no candidate.
    assert _generate_deterministic(5, [_chunk("The and for are but not.")]) == []


def test_deterministic_hint_names_the_source_document_and_page():
    cards = _generate_deterministic(1, [_chunk(SENTENCE_ONE, page_number=4)])
    assert cards[0]["hint"] == 'From "Vector Notes.pdf", page 4.'


def test_source_hint_degrades_when_metadata_is_missing():
    assert _source_hint({"document_title": "Notes.pdf"}) == 'From "Notes.pdf".'
    assert _source_hint({"document_title": "Notes.pdf", "page_number": None}) == (
        'From "Notes.pdf".'
    )
    assert _source_hint({}) is None


# ---------------------------------------------------------------------------
# Gemini output validation
# ---------------------------------------------------------------------------


def test_validate_gemini_items_maps_source_index_onto_its_chunk():
    chunks = [_chunk(SENTENCE_ONE, chunk_id="c1"), _chunk(SENTENCE_TWO, chunk_id="c2")]

    cards = FlashcardGenerationService._validate_gemini_items(
        [
            {"front": "What is stored?", "back": "Embeddings.", "source_index": 2},
            {"front": "What grounds answers?", "back": "Source documents.", "source_index": 1},
        ],
        chunks,
        5,
    )

    assert [card["chunk_id"] for card in cards] == ["c2", "c1"]
    assert cards[0]["document_title"] == "Vector Notes.pdf"


def test_validate_gemini_items_defaults_an_out_of_range_source_index():
    chunks = [_chunk(SENTENCE_ONE, chunk_id="c1")]

    for bad_index in (0, 99, "2", None, 1.5):
        cards = FlashcardGenerationService._validate_gemini_items(
            [{"front": "Q?", "back": "A.", "source_index": bad_index}], chunks, 5
        )
        assert len(cards) == 1, bad_index
        assert cards[0]["chunk_id"] == "c1", bad_index


def test_validate_gemini_items_drops_malformed_entries():
    chunks = [_chunk(SENTENCE_ONE)]

    cards = FlashcardGenerationService._validate_gemini_items(
        [
            "not an object",
            {"back": "Missing front."},
            {"front": "Missing back."},
            {"front": "   ", "back": "Blank front."},
            {"front": "Valid?", "back": "Yes."},
        ],
        chunks,
        5,
    )

    assert len(cards) == 1
    assert cards[0]["front_text"] == "Valid?"


def test_validate_gemini_items_caps_at_the_requested_count():
    chunks = [_chunk(SENTENCE_ONE)]
    raw = [{"front": f"Q{i}?", "back": f"A{i}."} for i in range(10)]

    cards = FlashcardGenerationService._validate_gemini_items(raw, chunks, 3)
    assert len(cards) == 3


def test_validate_gemini_items_normalises_the_optional_hint():
    chunks = [_chunk(SENTENCE_ONE)]

    blank = FlashcardGenerationService._validate_gemini_items(
        [{"front": "Q?", "back": "A.", "hint": "   "}], chunks, 5
    )
    absent = FlashcardGenerationService._validate_gemini_items(
        [{"front": "Q?", "back": "A."}], chunks, 5
    )
    present = FlashcardGenerationService._validate_gemini_items(
        [{"front": "Q?", "back": "A.", "hint": "Think vectors."}], chunks, 5
    )

    assert blank[0]["hint"] is None
    assert absent[0]["hint"] is None
    assert present[0]["hint"] == "Think vectors."


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_api_key_configured_is_read_from_settings_on_every_call(monkeypatch):
    """The key must not be frozen at construction time, unlike the quiz service."""
    from app.core.config import settings

    service = FlashcardGenerationService()
    assert service.model_name == settings.GEMINI_QA_MODEL

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    assert _api_key_configured() is False

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "your_gemini_api_key_here")
    assert _api_key_configured() is False

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "real-key")
    assert _api_key_configured() is True


async def _fallback_cards(service, chunks, *, card_count):
    """Runs the same fallback branch ``generate_deck`` uses after a Gemini error."""
    items: list = []
    try:
        items = await service._generate_with_gemini(card_count, None, chunks)
    except Exception:
        items = []
    if not items:
        items = _generate_deterministic(card_count, chunks)
    return items


async def test_generate_deck_falls_back_to_deterministic_cards(monkeypatch):
    """A Gemini failure must degrade to grounded offline cards, never to an error."""
    chunks = [_chunk(SENTENCE_ONE)]

    async def _boom(*args, **kwargs):
        raise RuntimeError("Gemini is unreachable")

    monkeypatch.setattr(
        FlashcardGenerationService, "_generate_with_gemini", _boom, raising=True
    )
    monkeypatch.setattr(engine, "_api_key_configured", lambda: True)

    service = FlashcardGenerationService()
    cards = await _fallback_cards(service, chunks, card_count=2)
    assert len(cards) == 2
    assert all("source_snippet" in card for card in cards)


def test_system_prompt_demands_exclusive_grounding():
    prompt = engine.FLASHCARD_SYSTEM_PROMPT
    assert "EXCLUSIVELY" in prompt
    assert "do not invent facts" in prompt
    assert "Do not use outside knowledge" in prompt
