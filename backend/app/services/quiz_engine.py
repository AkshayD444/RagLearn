import json
import random
import re
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.quiz import Quiz, QuizQuestion
from app.models.subject import Subject
from app.rag.retriever import retrieve_relevant_chunks


class NoStudyMaterialsError(Exception):
    """Raised when a subject has no indexed chunks to build questions from."""


GEMINI_SYSTEM_PROMPT = (
    "You are an exam question generator. "
    "Generate multiple-choice questions based EXCLUSIVELY on the provided study-material excerpts. "
    "Every fact in every question, option, and explanation must come from the excerpts. "
    "Do not use outside knowledge, do not invent facts, and do not create questions that "
    "cannot be answered from the excerpts alone."
)

_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had",
    "her", "was", "one", "our", "out", "has", "have", "this", "that", "these",
    "those", "with", "from", "they", "will", "would", "there", "their", "what",
    "about", "which", "when", "your", "how", "into", "than", "then", "them",
    "some", "more", "very", "such", "only", "also", "each", "other", "been",
    "were", "does", "should", "could", "because", "between", "during", "after",
    "before", "under", "over", "most", "both", "same", "through", "while",
    "where", "being", "may", "make", "made", "within", "using", "used", "each",
}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z-]+")

# Running headers/footers are extracted by pypdf as ordinary body text, so they
# arrive inside chunk content and score well against a subject-title query. Left
# alone they become the highest-value "sentences" in the chunk and produce
# cloze items like 'Computer Systems ___ -- Module 1/2: I/O Organisation Page 3'.
#
# Detection is deliberately narrow and evidence-based rather than a blocklist of
# title words, because real study sentences legitimately contain words like
# "systems" or "organisation". A sentence is dropped only when it shows several
# independent structural signals at once.
_METADATA_LABELS = re.compile(
    r"\b(page|pages|module|modules|chapter|unit|section|slide|lecture|week|"
    r"part|appendix|annex|figure|fig|table|exhibit|version|rev|"
    r"copyright|all\s+rights\s+reserved|printed|print)\b\.?",
    re.IGNORECASE,
)
_BARE_PAGE_NUMBER_RE = re.compile(r"(?<!\w)\d{1,4}\s*$")
_WHITESPACE_RE = re.compile(r"\s+")
# Lowercase function words are the signal that a line is prose. Prose sentences
# use them; a running title ("Computer Systems -- Module 1/2") has none.
_PROSE_MARKERS = frozenset(
    """a an the of and or but if then than that this these those is are was were be
    been being to in on at by for with from as it its into over under between
    during each every any all some such not can will would may might must should
    do does did have has had you your we our they their he she""".split()
)


def _is_metadata_boilerplate(sentence: str) -> bool:
    """True for running titles, page labels and similar layout text.

    Requires at least two independent signals so a genuine study sentence that
    happens to mention a page number is not discarded:

    1. A layout label ("Page 3", "Module 1"), or a line that is mostly digits.
    2. Almost no lowercase prose markers -- i.e. it reads like a heading.
    3. An em/en dash or pipe used to bolt the title onto the label, which is how
       most running heads are laid out on the page.

    A single signal is not enough. "The memory mapped interface registers appear
    on page 4 of the manual." contains a label but is plainly prose, and it must
    survive.
    """
    labels = len(_METADATA_LABELS.findall(sentence))
    digit_ratio = sum(ch.isdigit() for ch in sentence) / max(len(sentence), 1)
    has_separator = bool(re.search(r"[—–|]", sentence))
    words = _WHITESPACE_RE.split(sentence)
    prose_markers = sum(1 for w in words if w.strip(".,:;!?()").lower() in _PROSE_MARKERS)

    signals = 0
    # A label is meaningful only if it is followed by a number ("page 4"), not
    # the word "page" used in ordinary prose.
    if labels and re.search(r"\b\d", sentence):
        signals += 1
    if digit_ratio > 0.08 or (labels and _BARE_PAGE_NUMBER_RE.search(sentence)):
        signals += 1
    if has_separator:
        signals += 1
    if prose_markers <= 1:
        signals += 1

    # Two signals minimum. "page" alone, or a dash alone, never removes content.
    return signals >= 2


def _extract_sentences(chunks: List[dict]) -> List[tuple]:
    """Splits chunk content into sentences of reasonable question length.

    Layout boilerplate (running titles, "Page 4", "Module 1/2") is dropped here
    so it can never become a question stem or a distractor. Substantive prose
    from the very same chunk is kept.
    """
    sentences = []
    for chunk in chunks:
        content = chunk.get("content") or ""
        for raw in _SENTENCE_SPLIT_RE.split(content):
            sentence = _WHITESPACE_RE.sub(" ", raw).strip()
            words = sentence.split()
            if not (4 <= len(words) <= 40):
                continue
            if _is_metadata_boilerplate(sentence):
                continue
            sentences.append((sentence, chunk))
    return sentences


def _candidate_tokens(sentence: str) -> List[str]:
    """Meaningful, blankable tokens from a sentence (longest first)."""
    tokens = []
    seen = set()
    for match in _TOKEN_RE.findall(sentence):
        token = match.strip("-").lower()
        if len(token) < 4 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    tokens.sort(key=len, reverse=True)
    return tokens


def _distractor_pool(chunks: List[dict]) -> List[str]:
    """Candidate distractor terms drawn from the subject's own retrieved content.

    Built from sentences that survived the boilerplate filter rather than from
    raw chunk text. The two must stay in step: when a running header was a
    question stem, its words ("module", "systems", "computer") also filled the
    option list, which is why every distractor was a word from the title.

    Only the boilerplate filter applies here. The 4-40 word floor in
    `_extract_sentences` is a question-length heuristic, and a distractor term
    is equally usable from a short phrase, so it is deliberately not reused.
    """
    pool = []
    seen = set()
    for chunk in chunks:
        content = chunk.get("content") or ""
        for raw in _SENTENCE_SPLIT_RE.split(content):
            sentence = _WHITESPACE_RE.sub(" ", raw).strip()
            if not sentence or _is_metadata_boilerplate(sentence):
                continue
            for match in _TOKEN_RE.findall(sentence):
                token = match.strip("-").lower()
                if len(token) < 4 or token in _STOPWORDS or token in seen:
                    continue
                seen.add(token)
                pool.append(token)
    return pool


def _item_from_chunk(
    question_text: str,
    options: List[str],
    correct_index: int,
    explanation: str,
    difficulty: str,
    chunk: dict,
) -> dict:
    """Assembles a QuizQuestion-compatible dict with source citation metadata."""
    return {
        "question_text": question_text,
        "option_a": options[0],
        "option_b": options[1],
        "option_c": options[2],
        "option_d": options[3],
        "correct_option": "ABCD"[correct_index],
        "explanation": explanation,
        "difficulty": difficulty,
        "document_id": chunk.get("document_id"),
        "chunk_id": chunk.get("chunk_id"),
        "document_title": chunk.get("document_title"),
        "page_number": chunk.get("page_number"),
        "source_snippet": (chunk.get("content") or "")[:400],
    }


def _generate_deterministic(
    question_count: int, difficulty: str, chunks: List[dict]
) -> List[dict]:
    """Offline fallback: builds grounded cloze (fill-in-the-blank) MCQs whose
    question sentences and options come only from the retrieved chunk content."""
    sentences = _extract_sentences(chunks)
    pool = _distractor_pool(chunks)

    candidates = []
    seen = set()
    for sentence, chunk in sentences:
        for token in _candidate_tokens(sentence):
            key = (sentence, token)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((token, sentence, chunk))

    items = []
    for index, (token, sentence, chunk) in enumerate(candidates[:question_count]):
        distractors = []
        for candidate in pool:
            if candidate == token or candidate in distractors:
                continue
            distractors.append(candidate)
            if len(distractors) == 3:
                break
        if len(distractors) < 3:
            continue

        options = [token] + distractors
        rng = random.Random(f"{index}:{token}:{sentence}")
        rng.shuffle(options)
        correct_index = options.index(token)

        blanked, _ = re.subn(
            re.escape(token), "_____", sentence, count=1, flags=re.IGNORECASE
        )
        question_text = f'Complete the sentence: "{blanked}"'
        explanation = (
            f'The correct answer is "{token}". '
            f'The full sentence from your study material reads: "{sentence}"'
        )
        items.append(
            _item_from_chunk(
                question_text, options, correct_index, explanation, difficulty, chunk
            )
        )
        if len(items) >= question_count:
            break

    return items


def _parse_json_array(text: str) -> list:
    """Tolerant JSON-array parser (handles markdown code fences and prose wrappers)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("Model response was not a JSON array.")
    return parsed


def _build_context(chunks: List[dict], max_chunks: int, max_chars: int) -> str:
    """Serializes top chunks into numbered [Source N] excerpts under a char budget."""
    snippets = []
    total_chars = 0
    for i, chunk in enumerate(chunks[:max_chunks], start=1):
        snippet = (
            f'[Source {i}] Document: "{chunk["document_title"]}" | '
            f"Page {chunk['page_number']}\n{chunk['content']}"
        )
        total_chars += len(snippet)
        if total_chars > max_chars:
            break
        snippets.append(snippet)
    return "\n\n".join(snippets)


class QuizGenerationService:
    """Generates grounded MCQ quizzes from a user's own indexed document chunks."""

    def __init__(self):
        self.model_name = settings.GEMINI_QA_MODEL
        self.max_context_chunks = 20
        self.max_context_chars = 16000
        self.api_key_configured = bool(
            settings.GEMINI_API_KEY
            and settings.GEMINI_API_KEY != "your_gemini_api_key_here"
        )

    async def generate_quiz(
        self,
        session: AsyncSession,
        *,
        subject: Subject,
        question_count: int,
        difficulty: str,
        topic: Optional[str],
    ) -> Quiz:
        """Retrieves subject context, generates grounded questions, and persists the quiz."""
        query = (topic or "").strip() or subject.title or "study material"
        retrieval_top_k = min(max(question_count * 3, 9), 20)

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
        if self.api_key_configured:
            try:
                items = await self._generate_with_gemini(
                    question_count, difficulty, topic, chunks
                )
            except Exception as e:
                print(
                    f"[QuizGenerationService] Gemini API warning: {e}. "
                    "Using deterministic fallback."
                )
        if not items:
            items = _generate_deterministic(question_count, difficulty, chunks)
        if not items:
            raise NoStudyMaterialsError()

        quiz = Quiz(
            user_id=subject.user_id,
            subject_id=subject.id,
            title=f"{subject.title} Quiz",
            difficulty=difficulty,
            question_count=len(items),
            topic=topic,
        )
        session.add(quiz)
        await session.flush()

        for item in items:
            session.add(QuizQuestion(quiz_id=quiz.id, topic=topic, **item))

        await session.commit()
        await session.refresh(quiz)
        return quiz

    async def _generate_with_gemini(
        self,
        question_count: int,
        difficulty: str,
        topic: Optional[str],
        chunks: List[dict],
    ) -> List[dict]:
        from google import genai

        context = _build_context(
            chunks, self.max_context_chunks, self.max_context_chars
        )
        topic_line = f"\nFocus the questions on this topic: {topic}." if topic else ""
        prompt = (
            f"{GEMINI_SYSTEM_PROMPT}\n\n"
            f"Number of questions: {question_count}\n"
            f"Difficulty: {difficulty}{topic_line}\n\n"
            f"STUDY MATERIAL EXCERPTS:\n{context}\n\n"
            "Respond with ONLY a JSON array (no markdown fences, no prose) containing "
            f"exactly {question_count} objects. Each object must have these keys:\n"
            '  "question": string,\n'
            '  "options": array of exactly 4 strings,\n'
            '  "correct_index": integer 0-3 (the index of the correct option),\n'
            '  "explanation": string,\n'
            '  "difficulty": "easy" | "medium" | "hard",\n'
            '  "source_index": integer (1-based index of the [Source N] excerpt used)\n'
        )

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(model=self.model_name, contents=prompt)
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini returned an empty response.")

        raw_items = _parse_json_array(text)
        return self._validate_gemini_items(
            raw_items, chunks, question_count, difficulty
        )

    @staticmethod
    def _validate_gemini_items(
        raw_items: list,
        chunks: List[dict],
        question_count: int,
        requested_difficulty: str,
    ) -> List[dict]:
        """Validates model output and maps each item onto a real retrieved chunk."""
        items: List[dict] = []
        for entry in raw_items:
            if len(items) >= question_count:
                break
            if not isinstance(entry, dict):
                continue

            question = str(entry.get("question", "")).strip()
            explanation = str(entry.get("explanation", "")).strip()
            options_raw = entry.get("options")
            correct_index = entry.get("correct_index")
            source_index = entry.get("source_index")
            item_difficulty = str(entry.get("difficulty", "")).strip().lower()

            if not question or not explanation:
                continue
            if not isinstance(options_raw, list) or len(options_raw) != 4:
                continue
            options = [str(option).strip() for option in options_raw]
            if any(not option for option in options):
                continue
            if not isinstance(correct_index, int) or not 0 <= correct_index <= 3:
                continue
            if item_difficulty not in ("easy", "medium", "hard"):
                item_difficulty = requested_difficulty
            if not isinstance(source_index, int) or not 1 <= source_index <= len(chunks):
                source_index = 1

            chunk = chunks[source_index - 1]
            items.append(
                _item_from_chunk(
                    question,
                    options,
                    correct_index,
                    explanation,
                    item_difficulty,
                    chunk,
                )
            )
        return items


quiz_generation_service = QuizGenerationService()