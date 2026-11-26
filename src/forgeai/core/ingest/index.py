"""Pure-Python BM25 + TF-IDF cosine hybrid search index over KnowledgeIndexData."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from forgeai.core.schemas import DocumentChunk, KnowledgeIndexData

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "have", "in", "is", "it", "of", "on", "or", "that", "the", "to", "was",
        "were", "will", "with", "this", "what", "how", "when", "where", "who",
    }
)


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_RE.findall(text) if tok.lower() not in _STOPWORDS]


@dataclass(frozen=True)
class SearchHit:
    """Single ranked retrieval hit."""

    chunk: DocumentChunk
    score: float


class HybridKnowledgeIndex:
    """In-memory BM25 + TF-IDF cosine retriever backed by KnowledgeIndexData."""

    def __init__(self, data: KnowledgeIndexData, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.data = data
        self.k1 = k1
        self.b = b
        self._doc_tokens: list[list[str]] = []
        self._doc_tf: list[Counter[str]] = []
        self._df: Counter[str] = Counter()
        self._avgdl: float = 1.0
        self._idf: dict[str, float] = {}
        self._doc_norms: list[float] = []
        self._build()

    def _build(self) -> None:
        chunks = self.data.chunks
        if not chunks:
            return
        total_len = 0
        for chunk in chunks:
            tokens = tokenize(f"{chunk.section} {chunk.text}")
            self._doc_tokens.append(tokens)
            tf = Counter(tokens)
            self._doc_tf.append(tf)
            total_len += len(tokens)
            for term in tf:
                self._df[term] += 1

        n_docs = len(chunks)
        self._avgdl = max(1.0, total_len / n_docs)
        for term, df in self._df.items():
            # Smooth BM25/TF-IDF IDF
            self._idf[term] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)

        for tf in self._doc_tf:
            norm_sq = sum((count * self._idf.get(t, 0.0)) ** 2 for t, count in tf.items())
            self._doc_norms.append(math.sqrt(norm_sq) or 1.0)

    def search(self, query: str, *, top_k: int = 5, source_filter: str | None = None) -> list[SearchHit]:
        """Rank chunks by weighted BM25 (0.7) + TF-IDF cosine (0.3) score."""
        if not self.data.chunks:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return [SearchHit(chunk=c, score=0.1) for c in self.data.chunks[:top_k]]

        q_tf = Counter(q_tokens)
        q_norm = math.sqrt(sum((c * self._idf.get(t, 0.5)) ** 2 for t, c in q_tf.items())) or 1.0

        hits: list[SearchHit] = []
        for idx, chunk in enumerate(self.data.chunks):
            if source_filter and source_filter.lower() not in chunk.source.lower():
                continue
            tf = self._doc_tf[idx]
            dl = len(self._doc_tokens[idx]) or 1

            bm25 = 0.0
            dot = 0.0
            for term, q_count in q_tf.items():
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                idf = self._idf.get(term, 0.5)
                denom = freq + self.k1 * (1.0 - self.b + self.b * (dl / self._avgdl))
                bm25 += idf * (freq * (self.k1 + 1.0)) / denom
                dot += (freq * idf) * (q_count * idf)

            cosine = dot / (self._doc_norms[idx] * q_norm) if dot > 0 else 0.0
            score = 0.7 * bm25 + 0.3 * cosine
            if score > 0:
                hits.append(SearchHit(chunk=chunk, score=round(score, 4)))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def get_chunk(self, source: str, chunk_id: str) -> DocumentChunk | None:
        for chunk in self.data.chunks:
            if chunk.source == source and chunk.chunk_id == chunk_id:
                return chunk
        return None
