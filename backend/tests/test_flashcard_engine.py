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

    # Two definitional sentences -> two concepts -> two cards. The old cloze
    # generator answered any card_count by re-cutting the first sentence, which
    # is exactly the repetition this deck shape is meant to remove.
    assert len(cards) == 2
    for card in cards:
        # Every card pairs a question with the sentence that answers it, so the
        # answer side is always drawn from the student's own material.
        assert card["front_text"].strip()
        assert card["back_text"].strip()
        assert card["front_text"].startswith(
            ("What is", "What does", "How does", "What are", "What happens")
        )


def test_deterministic_cards_never_introduce_terms_absent_from_the_source():
    """The core grounding guarantee: no answer text outside the retrieved text."""
    source = f"{SENTENCE_ONE} {SENTENCE_TWO}"
    cards = _generate_deterministic(20, [_chunk(source)])

    assert cards
    for card in cards:
        # The answer is the source sentence itself, or the cloze fallback whose
        # back embeds that same sentence verbatim after the answer term.
        assert source.split(". ")[0] in card["back_text"] or card["back_text"] in source


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
    # Two distinct concepts: the deck now emits one card per concept, so a single
    # definitional sentence would correctly yield a single card.
    chunks = [_chunk(f"{SENTENCE_ONE} {SENTENCE_TWO}")]

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


# ---------------------------------------------------------------------------
# Regression: a deck must not be one cloze template cut from a single sentence
#
# The original fallback produced "Complete the statement: <sentence with one
# word removed>" once per blankable word, sentence-major. A request for 10 cards
# was satisfied entirely from the first sentence, so a real deck arrived as ten
# near-identical cards covering one idea -- e.g. "Direct Memory Access" blanked
# at "peripheral", then "transfer", then "directly", ...
# ---------------------------------------------------------------------------

DMA_PASSAGE = (
    "Direct Memory Access allows a peripheral device to transfer data directly "
    "to memory without passing through the CPU. "
    "The DMA controller requires the address of the memory location and the "
    "number of bytes to transfer. "
    "A DMA transfer proceeds by the device asserting a bus request, the "
    "controller granting the bus, and the transfer completing with an interrupt. "
    "The CPU performs one instruction to start a DMA operation. "
    "Memory mapped I/O uses the same address space as instructions. "
    "Interrupt driven I/O avoids the need for continuous polling. "
    "Polling requires the processor to check device status repeatedly."
)


def _dma_chunk() -> dict:
    return _chunk(DMA_PASSAGE)


def test_a_deck_is_not_repeated_single_word_cloze_extraction():
    """The direct regression: 10 requested cards, but not 10 blanks of one line."""
    cards = _generate_deterministic(10, [_dma_chunk()])

    assert cards
    cloze = [c for c in cards if c["front_text"].startswith("Complete the statement:")]
    assert not cloze, f"{len(cloze)} card(s) were still built by blanking one word"


def test_cards_are_drawn_from_several_distinct_source_sentences():
    """One sentence must never yield the whole deck again."""
    cards = _generate_deterministic(10, [_dma_chunk()])

    answers = {c["back_text"] for c in cards}
    assert len(answers) == len(cards)
    assert len(answers) >= 4, "deck covered too little of the passage"


def test_cards_cover_different_concepts():
    """Each card should ask about a different subject drawn from the passage."""
    cards = _generate_deterministic(10, [_dma_chunk()])

    fronts = [c["front_text"] for c in cards]
    assert len(set(fronts)) == len(fronts)

    subjects = [
        f.split("?")[0]
        for f in fronts
        if f.startswith(("What is ", "What does ", "How does ", "What happens"))
    ]
    # Not the same subject re-asked with different wording.
    assert len(set(subjects)) == len(subjects)


def test_concept_cards_ask_a_question_rather_than_a_gap():
    """Fronts are questions about concepts, not sentences with a hole in them."""
    cards = _generate_deterministic(10, [_dma_chunk()])

    for card in cards:
        front = card["front_text"]
        assert front.endswith("?") or front.startswith("Complete the statement:")
        if front.endswith("?"):
            assert "_____" not in front
            assert not front.startswith("Complete")


def test_card_answers_are_verbatim_source_sentences():
    """Grounding: the answer side must exist in the uploaded material."""
    cards = _generate_deterministic(10, [_dma_chunk()])

    for card in cards:
        assert card["back_text"] in DMA_PASSAGE


def test_cards_keep_their_source_citations():
    """Requirement: citations survive the change to concept questions."""
    chunk = _dma_chunk()
    cards = _generate_deterministic(10, [chunk])

    for card in cards:
        assert card["document_id"] == chunk["document_id"]
        assert card["chunk_id"] == chunk["chunk_id"]
        assert card["document_title"] == chunk["document_title"]
        assert card["page_number"] == chunk["page_number"]
        assert card["source_snippet"] == chunk["content"][:400]
        assert card["hint"] == 'From "Vector Notes.pdf", page 3.'


def test_process_sentences_get_a_process_question():
    """A sequence sentence supports "how", not a static definition."""
    cards = _generate_deterministic(10, [_dma_chunk()])

    backs = [c["back_text"] for c in cards]
    fronts = [c["front_text"] for c in cards]
    transfer_cards = [
        f for f, b in zip(fronts, backs) if b.startswith("A DMA transfer proceeds")
    ]
    assert transfer_cards, "the transfer sentence should have produced a card"
    assert any(f.startswith("How does") for f in transfer_cards)


def test_header_only_material_still_produces_no_cards():
    """The metadata/header filter from the quiz fix must still apply here."""
    header = "Computer Systems — Module 1/2: I/O Organisation Page 3."
    assert _generate_deterministic(10, [_chunk(header)]) == []


def test_definitional_verb_detection_tolerates_inflection():
    """Lecture prose is third-person: "allows", not "allow"."""
    from app.services.flashcard_engine import _is_definitional_verb

    assert _is_definitional_verb("allows") == "role"
    assert _is_definitional_verb("requires") == "role"
    assert _is_definitional_verb("uses") == "role"
    assert _is_definitional_verb("proceeds") == "process"
    assert _is_definitional_verb("avoids") == "role"
    assert _is_definitional_verb("Page") is None
    assert _is_definitional_verb("the") is None


def test_leading_noun_phrase_stops_at_the_verb():
    """The subject must not swallow the rest of the sentence.

    Scanning the whole sentence for the first known verb produced subjects like
    "CPU performs one instruction to", which generated the nonsense question
    "What is CPU performs one instruction to?".
    """
    from app.services.flashcard_engine import _leading_noun_phrase

    assert (
        _leading_noun_phrase("Direct Memory Access allows a peripheral device to transfer data.")
        == "Direct Memory Access"
    )
    assert (
        _leading_noun_phrase("The CPU performs one instruction to start the transfer.")
        == "CPU"
    )
    assert _leading_noun_phrase("The and for are but not.") is None


def test_leading_noun_phrase_rejects_an_overlong_run():
    """A definitional verb far from the start means there is no real subject.

    Without the length cap, "The processor unit which sits inside the chassis
    and connects to the system bus uses arbitration" would yield the subject
    "processor unit which sits inside the chassis and connects to the system
    bus" and a question no student could parse.
    """
    from app.services.flashcard_engine import _leading_noun_phrase

    long_run = (
        "The processor unit which sits inside the chassis and connects to the "
        "system bus uses arbitration."
    )
    assert _leading_noun_phrase(long_run) is None


def test_gemini_prompt_forbids_the_blank_the_word_pattern():
    """The model path gets the same diversity rule as the fallback."""
    prompt = engine.FLASHCARD_SYSTEM_PROMPT
    assert "removing a" in prompt
    assert "distinct concept" in prompt
    assert "Running headers" in prompt
