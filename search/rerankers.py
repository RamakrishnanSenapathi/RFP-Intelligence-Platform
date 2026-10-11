"""Re-rankers: Gemini listwise LLM re-ranker (default), optional cross-encoder, or none."""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from config import Settings
from utils import truncate

log = logging.getLogger("rfp.rerank")


class Reranker(Protocol):
    def rerank(self, query: str, hits: list, top_k: int) -> list: ...


class NoReranker:
    def rerank(self, query: str, hits: list, top_k: int) -> list:
        return hits[:top_k]


class RankOut(BaseModel):
    ranking: list[int] = Field(description="Passage numbers, most useful first")


class LLMReranker:
    """One LLM call ranks up to ~15 candidate passages (listwise). Falls back to input order on failure."""

    SYSTEM = ("You are a search re-ranker for procurement / RFP documents. Rank passages by how directly "
              "they answer the query. Passages from addenda that change a value outrank the original value "
              "only when the query is about the final/current value.")

    def __init__(self, llm_getter):
        self._get = llm_getter

    def rerank(self, query: str, hits: list, top_k: int) -> list:
        if len(hits) <= 1:
            return hits
        lines = []
        for i, h in enumerate(hits, 1):
            c = h.chunk
            tag = c["doc_type"] + (f" #{c['addendum_number']}" if c.get("addendum_number") else "")
            lines.append(f"[{i}] ({tag}, {c['file_name']}, p{c['page_number']})\n{truncate(c['text'], 900)}")
        prompt = (f"Query: {query}\n\nPassages:\n" + "\n\n".join(lines) +
                  f"\n\nReturn the numbers of the {min(top_k, len(hits))} most relevant passages, best first.")
        try:
            out = self._get().generate_json(prompt, RankOut, system=self.SYSTEM)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM rerank failed, keeping fused order: %s", e)
            return hits[:top_k]
        seen, ordered = set(), []
        for n in out.ranking:
            if isinstance(n, int) and 1 <= n <= len(hits) and n not in seen:
                seen.add(n)
                ordered.append(hits[n - 1])
        ordered += [h for i, h in enumerate(hits, 1) if i not in seen]
        for rank, h in enumerate(ordered):
            h.scores["rerank_position"] = rank + 1
        return ordered[:top_k]


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder  # optional dependency

        self.model = CrossEncoder(model_name)

    def rerank(self, query: str, hits: list, top_k: int) -> list:
        if len(hits) <= 1:
            return hits
        scores = self.model.predict([(query, h.chunk["text"]) for h in hits])
        for h, s in zip(hits, scores):
            h.scores["cross_encoder"] = float(s)
        return sorted(hits, key=lambda h: -h.scores["cross_encoder"])[:top_k]


def get_reranker(settings: Settings) -> Reranker:
    b = settings.reranker_backend
    if b == "none":
        return NoReranker()
    if b == "cross_encoder":
        return CrossEncoderReranker(settings.cross_encoder_model)

    def getter():
        from llm import get_llm
        return get_llm()

    return LLMReranker(getter)
