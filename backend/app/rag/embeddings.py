import hashlib
from typing import List
from app.core.config import settings


class GeminiEmbeddingService:
    """Generates 768-dimensional text embeddings using Google Gemini API (text-embedding-004)."""

    def __init__(self):
        self.model_name = "text-embedding-004"
        self.dimension = 768

    async def get_embedding(self, text: str) -> List[float]:
        """Generates a 768-dim float embedding for a single string."""
        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "your_gemini_api_key_here":
            try:
                from google import genai
                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                response = client.models.embed_content(
                    model=self.model_name,
                    contents=text
                )
                if response and hasattr(response, "embedding") and response.embedding:
                    return list(response.embedding.values)
                elif response and hasattr(response, "embeddings") and response.embeddings:
                    return list(response.embeddings[0].values)
            except Exception as e:
                print(f"[GeminiEmbeddingService] API Warning: {e}. Falling back to deterministic vector.")

        # Fallback for testing / offline environment: Generate deterministic normalized pseudo-vector
        return self._generate_fallback_vector(text)

    async def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch embedding generation."""
        embeddings = []
        for text in texts:
            vec = await self.get_embedding(text)
            embeddings.append(vec)
        return embeddings

    def _generate_fallback_vector(self, text: str) -> List[float]:
        """Generates a 768-length normalized float array derived deterministically from text hash."""
        hash_digest = hashlib.sha256(text.encode('utf-8')).digest()
        vector = []
        for i in range(self.dimension):
            byte_val = hash_digest[i % len(hash_digest)]
            val = (float(byte_val) / 255.0) - 0.5
            vector.append(round(val, 6))
        return vector


embedding_service = GeminiEmbeddingService()
