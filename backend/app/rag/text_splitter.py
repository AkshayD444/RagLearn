import re
from typing import List, Dict, Any


class RecursiveTextSplitter:
    """Splits text into chunks by recursively attempting to break on natural boundaries."""

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        separators: List[str] = None
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", ". ", " ", ""]

    def split_pages(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Splits page texts into metadata-enriched chunks.

        Input format: [{"page_number": 1, "text": "..."}, ...]
        Output format: [{"chunk_index": 0, "page_number": 1, "content": "...", "token_count": 120}]
        """
        chunks = []
        global_index = 0

        for page in pages:
            page_num = page.get("page_number", 1)
            page_text = page.get("text", "").strip()

            if not page_text:
                continue

            page_chunks = self._split_text(page_text)
            for chunk_text in page_chunks:
                if not chunk_text.strip():
                    continue
                # Rough token estimate (~4 chars per token)
                estimated_tokens = max(1, len(chunk_text) // 4)
                chunks.append({
                    "chunk_index": global_index,
                    "page_number": page_num,
                    "content": chunk_text.strip(),
                    "token_count": estimated_tokens
                })
                global_index += 1

        return chunks

    def _split_text(self, text: str) -> List[str]:
        """Recursive helper to divide text into chunks under max size."""
        if len(text) <= self.chunk_size:
            return [text]

        # Find best separator
        separator = self.separators[-1]
        for s in self.separators:
            if s == "":
                separator = ""
                break
            if s in text:
                separator = s
                break

        if separator:
            splits = text.split(separator)
        else:
            splits = list(text)

        final_chunks = []
        current_chunk = []
        current_length = 0

        for split in splits:
            split_len = len(split) + len(separator)
            if current_length + split_len > self.chunk_size and current_chunk:
                joined = separator.join(current_chunk)
                final_chunks.append(joined)
                
                # Maintain overlap
                overlap_len = 0
                overlap_items = []
                for item in reversed(current_chunk):
                    if overlap_len + len(item) <= self.chunk_overlap:
                        overlap_items.insert(0, item)
                        overlap_len += len(item)
                    else:
                        break
                current_chunk = overlap_items
                current_length = sum(len(x) + len(separator) for x in current_chunk)

            current_chunk.append(split)
            current_length += split_len

        if current_chunk:
            final_chunks.append(separator.join(current_chunk))

        return final_chunks
