from typing import Dict, List

from app.core.config import settings

SYSTEM_PROMPT = (
    "You are an AI study tutor for a university student. "
    "Answer the student's question using ONLY the provided study-material excerpts. "
    "The excerpts are labeled [Source N]. Support claims with inline citations such as [1] or [2]. "
    "If the excerpts do not contain the answer, say so clearly and suggest what material is missing. "
    "Do not use outside knowledge or speculate."
)


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
        response = client.models.generate_content(model=self.model_name, contents=prompt)

        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini returned an empty response.")
        return text.strip()

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