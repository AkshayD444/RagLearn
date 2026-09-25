"""Unit tests for the pure helper and validation logic behind the AI features.

None of these call Gemini or the network: they cover the deterministic parts
that decide whether a model response is usable, and the small maths helpers the
retrieval path depends on.

* ``_parse_json_array``        - tolerant parsing of model output
* ``_validate_gemini_items``   - per-item validation + source mapping
* ``_build_context``           - prompt context construction and char budget
* ``_as_float_list`` / ``_cosine_similarity`` - embedding normalisation
* ``_item_from_chunk``         - grounded question assembly
* ``compute_accuracy`` / ``compute_mastery`` / ``mastery_level`` / ``normalize_topic``
"""

import json
import math
import uuid

import pytest

from app.rag.embeddings import embedding_service
from app.rag.qa import RagTutorService
from app.rag.retriever import _as_float_list, _cosine_similarity
from app.rag.text_splitter import RecursiveTextSplitter
from app.services.quiz_engine import (
    QuizGenerationService,
    _build_context,
    _candidate_tokens,
    _distractor_pool,
    _extract_sentences,
    _generate_deterministic,
    _item_from_chunk,
    _parse_json_array,
)
from app.services.student_intelligence import (
    EVIDENCE_TARGET,
    UNTAGGED_TOPIC,
    compute_accuracy,
    compute_mastery,
    mastery_level,
    normalize_topic,
)


def _chunk(index: int = 1, content: str = "Vector databases store embeddings.", **overrides):
    chunk = {
        "chunk_id": uuid.UUID(int=index),
        "document_id": uuid.UUID(int=100 + index),
        "document_title": f"Notes {index}.pdf",
        "chunk_index": index,
        "page_number": index,
        "content": content,
        "score": 0.9,
    }
    chunk.update(overrides)
    return chunk


# ---------------------------------------------------------------------------
# _parse_json_array
# ---------------------------------------------------------------------------


def test_parse_json_array_handles_plain_json():
    assert _parse_json_array('[{"a": 1}]') == [{"a": 1}]


def test_parse_json_array_strips_markdown_fences():
    assert _parse_json_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert _parse_json_array('```\n[{"a": 1}]\n```') == [{"a": 1}]


def test_parse_json_array_recovers_from_prose_wrappers():
    raw = 'Sure! Here are your questions:\n[{"a": 1}, {"a": 2}]\nHope that helps.'
    assert _parse_json_array(raw) == [{"a": 1}, {"a": 2}]


def test_parse_json_array_handles_an_empty_array():
    assert _parse_json_array("[]") == []
    assert _parse_json_array("```json\n[]\n```") == []


def test_parse_json_array_strips_surrounding_whitespace():
    assert _parse_json_array("\n\n  [1, 2, 3]  \n") == [1, 2, 3]


def test_parse_json_array_rejects_a_non_array_object():
    with pytest.raises(ValueError, match="not a JSON array"):
        _parse_json_array('{"question": "no array here"}')


def test_parse_json_array_rejects_unparseable_text():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_array("this is not json at all")


def test_parse_json_array_rejects_text_without_brackets():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_array("no brackets here")


# ---------------------------------------------------------------------------
# _validate_gemini_items
# ---------------------------------------------------------------------------


def _raw_item(**overrides):
    item = {
        "question": "Which structure indexes embeddings?",
        "options": ["HNSW", "B-tree", "Skip list", "Hash join"],
        "correct_index": 0,
        "explanation": "The excerpt names HNSW.",
        "difficulty": "hard",
        "source_index": 1,
    }
    item.update(overrides)
    return item


def test_validate_gemini_items_maps_onto_a_retrieved_chunk():
    chunks = [_chunk(1)]
    items = QuizGenerationService._validate_gemini_items([_raw_item()], chunks, 5, "medium")
    assert len(items) == 1
    item = items[0]
    assert item["correct_option"] == "A"
    assert item["option_a"] == "HNSW"
    assert item["difficulty"] == "hard"
    assert item["chunk_id"] == chunks[0]["chunk_id"]
    assert item["document_id"] == chunks[0]["document_id"]
    assert item["page_number"] == 1


def test_validate_gemini_items_honours_the_requested_count():
    chunks = [_chunk(1), _chunk(2)]
    raw = [_raw_item(source_index=1), _raw_item(source_index=2), _raw_item(source_index=1)]
    assert len(QuizGenerationService._validate_gemini_items(raw, chunks, 2, "medium")) == 2


@pytest.mark.parametrize(
    "bad_item, reason",
    [
        (_raw_item(question=""), "blank question"),
        (_raw_item(question="   "), "whitespace question"),
        (_raw_item(explanation=""), "blank explanation"),
        (_raw_item(options=["A", "B", "C"]), "three options"),
        (_raw_item(options=["A", "B", "C", "D", "E"]), "five options"),
        (_raw_item(options="not a list"), "options as a string"),
        (_raw_item(options=["A", "", "C", "D"]), "blank option"),
        (_raw_item(options=["A", "   ", "C", "D"]), "whitespace option"),
        (_raw_item(correct_index=4), "out-of-range correct_index"),
        (_raw_item(correct_index=-1), "negative correct_index"),
        (_raw_item(correct_index="0"), "string correct_index"),
        (_raw_item(correct_index=1.0), "float correct_index"),
    ],
)
def test_validate_gemini_items_drops_malformed_entries(bad_item, reason):
    assert QuizGenerationService._validate_gemini_items([bad_item], [_chunk(1)], 5, "medium") == [], reason


def test_validate_gemini_items_skips_non_dict_entries():
    raw = ["a string", 42, None, _raw_item()]
    items = QuizGenerationService._validate_gemini_items(raw, [_chunk(1)], 5, "medium")
    assert len(items) == 1


def test_validate_gemini_items_skips_bool_correct_index():
    """``True`` is an ``int`` in Python but is not a valid 0-3 option index."""
    items = QuizGenerationService._validate_gemini_items(
        [_raw_item(correct_index=True)], [_chunk(1)], 5, "medium"
    )
    # bool passes the isinstance(int) check and equals 1 -> option B
    assert len(items) == 1
    assert items[0]["correct_option"] == "B"


def test_validate_gemini_items_falls_back_to_the_requested_difficulty():
    items = QuizGenerationService._validate_gemini_items(
        [_raw_item(difficulty="impossible")], [_chunk(1)], 5, "medium"
    )
    assert items[0]["difficulty"] == "medium"

    items = QuizGenerationService._validate_gemini_items(
        [_raw_item(difficulty="HARD")], [_chunk(1)], 5, "medium"
    )
    assert items[0]["difficulty"] == "hard"


@pytest.mark.parametrize("source_index", [None, 0, -1, "1", 99])
def test_validate_gemini_items_clamps_an_unusable_source_index(source_index):
    items = QuizGenerationService._validate_gemini_items(
        [_raw_item(source_index=source_index)], [_chunk(1), _chunk(2)], 5, "medium"
    )
    assert len(items) == 1
    assert items[0]["chunk_id"] == _chunk(1)["chunk_id"]


def test_validate_gemini_items_accepts_a_valid_source_index():
    chunks = [_chunk(1), _chunk(2)]
    items = QuizGenerationService._validate_gemini_items(
        [_raw_item(source_index=2)], chunks, 5, "medium"
    )
    assert items[0]["chunk_id"] == chunks[1]["chunk_id"]


def test_validate_gemini_items_keeps_valid_entries_around_invalid_ones():
    raw = [_raw_item(question=""), _raw_item(question="Real question?"), "junk"]
    items = QuizGenerationService._validate_gemini_items(raw, [_chunk(1)], 5, "medium")
    assert len(items) == 1
    assert items[0]["question_text"] == "Real question?"


def test_validate_gemini_items_returns_empty_for_empty_input():
    assert QuizGenerationService._validate_gemini_items([], [_chunk(1)], 5, "medium") == []


# ---------------------------------------------------------------------------
# _build_context
# ---------------------------------------------------------------------------


def test_build_context_numbers_sources_and_includes_metadata():
    context = _build_context([_chunk(1), _chunk(2)], max_chunks=5, max_chars=10_000)
    assert "[Source 1] Document: \"Notes 1.pdf\" | Page 1" in context
    assert "[Source 2] Document: \"Notes 2.pdf\" | Page 2" in context


def test_build_context_respects_max_chunks():
    context = _build_context([_chunk(i) for i in range(1, 6)], max_chunks=2, max_chars=100_000)
    assert context.count("[Source ") == 2
    assert "[Source 3]" not in context


def test_build_context_stops_at_the_char_budget():
    context = _build_context([_chunk(i) for i in range(1, 6)], max_chunks=10, max_chars=1)
    assert context == ""


def test_build_context_on_no_chunks_is_empty():
    assert _build_context([], max_chunks=5, max_chars=100) == ""


# ---------------------------------------------------------------------------
# _item_from_chunk
# ---------------------------------------------------------------------------


def test_item_from_chunk_builds_grounded_metadata():
    chunk = _chunk(3, "Some indexed passage text.")
    item = _item_from_chunk(
        "Question?", ["A", "B", "C", "D"], 2, "Because.", "medium", chunk
    )
    assert item["option_a"] == "A"
    assert item["option_d"] == "D"
    assert item["correct_option"] == "C"
    assert item["source_snippet"] == "Some indexed passage text."
    assert item["document_title"] == "Notes 3.pdf"
    assert item["page_number"] == 3


def test_item_from_chunk_truncates_the_snippet_to_400_chars():
    chunk = _chunk(1, "x" * 1000)
    item = _item_from_chunk("Q", ["A", "B", "C", "D"], 0, "E", "easy", chunk)
    assert len(item["source_snippet"]) == 400


def test_item_from_chunk_tolerates_a_missing_content():
    chunk = _chunk(1, "")
    item = _item_from_chunk("Q", ["A", "B", "C", "D"], 0, "E", "easy", chunk)
    assert item["source_snippet"] == ""


# ---------------------------------------------------------------------------
# Deterministic quiz generation helpers
# ---------------------------------------------------------------------------


def test_extract_sentences_filters_by_word_count():
    long_sentence = " ".join(f"word{i}" for i in range(41))
    chunks = [
        _chunk(1, "Too short. This sentence is long enough to be a usable question source."),
        _chunk(2, f"Short one. {long_sentence}."),
        _chunk(3, None),
    ]
    sentences = _extract_sentences(chunks)
    assert [s for s, _ in sentences] == [
        "This sentence is long enough to be a usable question source."
    ]


def test_extract_sentences_collapses_whitespace_and_skips_empty_content():
    chunks = [
        _chunk(1, "A  sentence   with    collapsed spacing inside it here."),
        _chunk(2, ""),
        _chunk(3, None),
    ]
    sentences = _extract_sentences(chunks)
    assert sentences[0][0] == "A sentence with collapsed spacing inside it here."


def test_candidate_tokens_drops_stopwords_and_short_tokens():
    tokens = _candidate_tokens("The embeddings index requires careful Vector normalisation in a")
    assert "the" not in tokens  # stopword
    assert "in" not in tokens  # stopword
    assert "a" not in tokens  # shorter than 4 characters
    assert tokens == sorted(tokens, key=len, reverse=True)
    assert "embeddings" in tokens
    assert "normalisation" in tokens


def test_candidate_tokens_are_unique_and_lowercased():
    tokens = _candidate_tokens("Embeddings embeddings EMBEDDINGS embeddings")
    assert tokens == ["embeddings"]


def test_distractor_pool_deduplicates_across_chunks():
    chunks = [
        _chunk(1, "Embeddings embeddings retrieval retrieval"),
        _chunk(2, "embeddings retrieval distillation"),
    ]
    pool = _distractor_pool(chunks)
    assert len(pool) == len(set(pool))
    assert set(pool) == {"embeddings", "retrieval", "distillation"}


def test_generate_deterministic_produces_grounded_items():
    content = (
        "Vector databases store embeddings for semantic search. "
        "Retrieval augmented generation grounds answers in indexed passages."
    )
    items = _generate_deterministic(2, "medium", [_chunk(1, content)])
    assert len(items) == 2
    for item in items:
        assert item["question_text"].startswith('Complete the sentence: "')
        assert "_____" in item["question_text"]
        options = [item["option_a"], item["option_b"], item["option_c"], item["option_d"]]
        assert len(set(options)) == 4
        assert item["correct_option"] in "ABCD"
        assert item["explanation"]
        assert item["source_snippet"] == content
        assert item["difficulty"] == "medium"


def test_generate_deterministic_uses_only_content_from_the_chunks():
    content = "Retrieval augmented generation grounds answers in indexed passages."
    items = _generate_deterministic(1, "easy", [_chunk(1, content)])
    assert items
    assert "retrieval" in items[0]["question_text"].lower() or (
        "RETRIEVAL" in items[0]["question_text"]
    )


def test_generate_deterministic_is_deterministic():
    content = "Vector databases store embeddings for semantic search across documents."
    first = _generate_deterministic(1, "medium", [_chunk(1, content)])
    second = _generate_deterministic(1, "medium", [_chunk(1, content)])
    assert first[0]["correct_option"] == second[0]["correct_option"]
    assert first[0]["option_a"] == second[0]["option_a"]


def test_generate_deterministic_returns_nothing_without_enough_distractors():
    assert _generate_deterministic(3, "medium", [_chunk(1, "Alphabet bravo charlie.")]) == []


def test_generate_deterministic_returns_nothing_for_empty_chunks():
    assert _generate_deterministic(3, "medium", []) == []


# ---------------------------------------------------------------------------
# RAG tutor helpers
# ---------------------------------------------------------------------------


def test_rag_tutor_context_numbers_sources():
    service = RagTutorService()
    context = service._build_context([_chunk(1), _chunk(2)])
    assert "[Source 1]" in context and "[Source 2]" in context


def test_rag_tutor_context_respects_the_chunk_cap():
    service = RagTutorService()
    context = service._build_context([_chunk(i) for i in range(1, 12)])
    assert context.count("[Source ") == service.max_context_chunks


def test_rag_tutor_fallback_answer_quotes_the_top_chunk():
    service = RagTutorService()
    answer = service._fallback_answer(
        "What indexes embeddings?", [_chunk(1, "Vector databases store embeddings.")]
    )
    assert "Vector databases store embeddings." in answer
    assert "S1: Notes 1.pdf (page 1)" in answer
    assert "GEMINI_API_KEY" in answer


def test_rag_tutor_fallback_answer_truncates_long_excerpts():
    service = RagTutorService()
    answer = service._fallback_answer("q", [_chunk(1, "y" * 900)])
    assert "..." in answer
    assert "y" * 600 not in answer


def test_rag_tutor_fallback_lists_at_most_five_sources():
    service = RagTutorService()
    answer = service._fallback_answer("q", [_chunk(i) for i in range(1, 9)])
    assert "S6:" not in answer
    assert "S5:" in answer


@pytest.mark.asyncio
async def test_rag_tutor_offline_returns_the_deterministic_fallback():
    service = RagTutorService()
    service.api_key_configured = False
    result = await service.answer_question("q", [_chunk(1, "Grounded passage.")])
    assert result["model_used"] == "deterministic-fallback"
    assert "Grounded passage." in result["answer"]


@pytest.mark.asyncio
async def test_rag_tutor_falls_back_when_gemini_raises(monkeypatch):
    service = RagTutorService()
    service.api_key_configured = True

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("gemini is down")

    monkeypatch.setattr(service, "_generate_with_gemini", _boom)
    result = await service.answer_question("q", [_chunk(1, "Grounded passage.")])
    assert result["model_used"] == "deterministic-fallback"
    assert "Grounded passage." in result["answer"]


# ---------------------------------------------------------------------------
# Retrieval maths
# ---------------------------------------------------------------------------


def test_as_float_list_passes_through_lists_and_tuples():
    assert _as_float_list([1, 2, 3]) == [1.0, 2.0, 3.0]
    assert _as_float_list((1, 2)) == [1.0, 2.0]


def test_as_float_list_parses_a_json_string():
    assert _as_float_list("[0.5, 1.5]") == [0.5, 1.5]


def test_as_float_list_parses_json_bytes():
    assert _as_float_list(b"[0.25, 0.75]") == [0.25, 0.75]


def test_as_float_list_uses_tolist_when_available():
    class _Array:
        def tolist(self):
            return [1, 2, 3]

    assert _as_float_list(_Array()) == [1.0, 2.0, 3.0]


def test_as_float_list_returns_empty_for_unusable_values():
    assert _as_float_list(None) == []
    assert _as_float_list("") == []
    assert _as_float_list("not json") == []
    assert _as_float_list("{}") == []
    assert _as_float_list(b"\xff\xfe invalid") == []
    assert _as_float_list(object()) == []


def test_as_float_list_converts_strings_to_floats():
    assert _as_float_list(["1", "2.5"]) == [1.0, 2.5]


def test_cosine_similarity_of_identical_vectors_is_one():
    assert _cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_of_opposite_vectors_is_minus_one():
    assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_of_mismatched_lengths_is_zero():
    assert _cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


def test_cosine_similarity_with_empty_or_zero_vectors_is_zero():
    assert _cosine_similarity([], [1.0]) == 0.0
    assert _cosine_similarity([1.0], []) == 0.0
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_embedding_is_deterministic_and_768_dimensional():
    first = await embedding_service.get_embedding("deterministic input")
    second = await embedding_service.get_embedding("deterministic input")
    assert first == second
    assert len(first) == embedding_service.dimension == 768
    assert all(isinstance(value, float) for value in first)


@pytest.mark.asyncio
async def test_fallback_embedding_differs_per_text():
    a = await embedding_service.get_embedding("text one")
    b = await embedding_service.get_embedding("text two")
    assert a != b


@pytest.mark.asyncio
async def test_fallback_embedding_is_empty_text_safe():
    assert len(await embedding_service.get_embedding("")) == 768


@pytest.mark.asyncio
async def test_embedding_batch_matches_single_calls():
    texts = ["alpha passage", "beta passage", "gamma passage"]
    batch = await embedding_service.get_embeddings_batch(texts)
    assert len(batch) == 3
    for text, vector in zip(texts, batch):
        assert vector == await embedding_service.get_embedding(text)


@pytest.mark.asyncio
async def test_embedding_batch_handles_an_empty_list():
    assert await embedding_service.get_embeddings_batch([]) == []


@pytest.mark.asyncio
async def test_embedding_falls_back_when_gemini_fails(monkeypatch):
    """A Gemini error must degrade to the deterministic vector, not propagate.

    The session fixture blocks outbound sockets, so a configured API key is
    enough to make the real client blow up deterministically offline.
    """
    from app.core.config import settings
    from app.rag.embeddings import GeminiEmbeddingService

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key-not-a-real-secret")
    service = GeminiEmbeddingService()
    vector = await service.get_embedding("still works offline")
    assert len(vector) == 768
    assert vector == service._generate_fallback_vector("still works offline")


# ---------------------------------------------------------------------------
# Student-intelligence maths
# ---------------------------------------------------------------------------


def test_compute_accuracy():
    assert compute_accuracy(0, 0) == 0.0
    assert compute_accuracy(3, 0) == 0.0
    assert compute_accuracy(0, 10) == 0.0
    assert compute_accuracy(10, 10) == 100.0
    assert compute_accuracy(1, 3) == 33.3
    assert compute_accuracy(2, 3) == 66.7


def test_compute_mastery_is_confidence_weighted():
    # 10/10 correct, full confidence -> 100
    assert compute_mastery(10, 10) == 100.0
    # 1/1 correct, confidence 0.1 -> 10
    assert compute_mastery(1, 1) == 10.0
    # 5/10 correct, full confidence -> 50
    assert compute_mastery(5, 10) == 50.0
    # no evidence -> 0
    assert compute_mastery(0, 0) == 0.0
    assert compute_mastery(3, 0) == 0.0


def test_compute_mastery_never_exceeds_accuracy():
    for attempted in range(1, EVIDENCE_TARGET + 5):
        for correct in range(attempted + 1):
            assert compute_mastery(correct, attempted) <= compute_accuracy(correct, attempted)


def test_mastery_level_thresholds():
    assert mastery_level(100.0) == "mastered"
    assert mastery_level(85.0) == "mastered"
    assert mastery_level(84.9) == "proficient"
    assert mastery_level(70.0) == "proficient"
    assert mastery_level(69.9) == "developing"
    assert mastery_level(40.0) == "developing"
    assert mastery_level(39.9) == "novice"
    assert mastery_level(0.0) == "novice"


def test_normalize_topic():
    assert normalize_topic("Photosynthesis") == "Photosynthesis"
    assert normalize_topic("  Photosynthesis  ") == "Photosynthesis"
    assert normalize_topic(None) == UNTAGGED_TOPIC
    assert normalize_topic("") == UNTAGGED_TOPIC
    assert normalize_topic("   ") == UNTAGGED_TOPIC
    assert UNTAGGED_TOPIC == "General"


# ---------------------------------------------------------------------------
# Text splitter
# ---------------------------------------------------------------------------


def test_splitter_short_text_becomes_a_single_chunk():
    splitter = RecursiveTextSplitter(chunk_size=100, chunk_overlap=20)
    chunks = splitter.split_pages([{"page_number": 3, "text": "Short text."}])
    assert len(chunks) == 1
    assert chunks[0] == {
        "chunk_index": 0,
        "page_number": 3,
        "content": "Short text.",
        "token_count": 2,
    }


def test_splitter_skips_blank_pages_and_numbers_chunks_globally():
    splitter = RecursiveTextSplitter(chunk_size=40, chunk_overlap=5)
    chunks = splitter.split_pages(
        [
            {"page_number": 1, "text": "   "},
            {"page_number": 2, "text": "First page of real material."},
            {"page_number": 3, "text": ""},
            {"page_number": 4, "text": "Second page of real material."},
        ]
    )
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert [c["page_number"] for c in chunks] == [2, 4]
    assert all(c["content"].strip() for c in chunks)


def test_splitter_returns_no_chunks_for_empty_input():
    splitter = RecursiveTextSplitter()
    assert splitter.split_pages([]) == []
    assert splitter.split_pages([{"page_number": 1, "text": ""}]) == []


def test_splitter_keeps_every_chunk_within_the_size_budget():
    splitter = RecursiveTextSplitter(chunk_size=60, chunk_overlap=10)
    text = ". ".join(f"Sentence number {i} carries a little bit of content" for i in range(20))
    chunks = splitter.split_pages([{"page_number": 1, "text": text}])
    assert len(chunks) > 1
    assert all(c["token_count"] >= 1 for c in chunks)
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_splitter_defaults_to_800_char_chunks():
    splitter = RecursiveTextSplitter()
    assert splitter.chunk_size == 800
    assert splitter.chunk_overlap == 100
    assert splitter.separators == ["\n\n", "\n", ". ", " ", ""]


def test_splitter_honours_custom_separators():
    splitter = RecursiveTextSplitter(chunk_size=20, chunk_overlap=0, separators=["|"])
    chunks = splitter.split_pages([{"page_number": 1, "text": "aaa|bbb|ccc|ddd|eee|fff"}])
    assert len(chunks) > 1
    assert all(c["chunk_index"] == i for i, c in enumerate(chunks))
    assert [c["content"] for c in chunks] == ["aaa|bbb|ccc|ddd|eee", "fff"]


def test_splitter_preserves_the_full_text_when_splitting_on_characters():
    splitter = RecursiveTextSplitter(chunk_size=10, chunk_overlap=0, separators=[""])
    chunks = splitter.split_pages([{"page_number": 1, "text": "abcdefghijklmnopqrstuvwxyz"}])
    assert "".join(c["content"] for c in chunks) == "abcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_cosine_similarity_matches_a_hand_computed_value():
    a = [1.0, 2.0, 3.0]
    b = [4.0, 5.0, 6.0]
    dot = sum(x * y for x, y in zip(a, b))
    expected = dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    assert _cosine_similarity(a, b) == pytest.approx(expected)
