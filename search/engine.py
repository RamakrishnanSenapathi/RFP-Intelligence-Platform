"""Part B – the RAG search engine: hybrid (dense + BM25) retrieval with RRF, metadata filters,
query expansion, optional re-ranking, citations and incremental indexing."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
from rank_bm25 import BM25Okapi

from config import Settings, get_settings
from ingestion.pipeline import folder_fingerprint, ingest_folder
from .embeddings import Embedder, get_embedder
from .query import expand_query, tokenize
from .rerankers import Reranker, get_reranker
from .store import IndexStore

log = logging.getLogger("rfp.search")

MODES = ("vector", "bm25", "hybrid", "hybrid_rerank")


@dataclass
class SearchHit:
    chunk: dict
    score: float
    scores: dict = field(default_factory=dict)

    def citation(self) -> dict:
        c = self.chunk
        return {"file": c["file_name"], "page": c["page_number"], "bid_id": c["bid_id"],
                "doc_type": c["doc_type"], "addendum_number": c.get("addendum_number"),
                "chunk_id": c["chunk_id"], "text": c["text"]}

    def to_dict(self) -> dict:
        return {**self.citation(), "section": self.chunk.get("section", ""),
                "score": round(float(self.score), 5), "scores": {k: round(float(v), 4) for k, v in self.scores.items()}}


def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion over several ranked lists of document indices."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


class SearchEngine:
    def __init__(self, settings: Optional[Settings] = None, embedder: Optional[Embedder] = None,
                 reranker: Optional[Reranker] = None, store: Optional[IndexStore] = None):
        self.settings = settings or get_settings()
        self._embedder = embedder
        self._reranker = reranker
        self.store = store or IndexStore(self.settings.index_dir)
        self._bm25: Optional[BM25Okapi] = None
        self._bm25_key: tuple = ()

    # lazily created so that read-only operations don't need an API key
    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_embedder(self.settings)
        return self._embedder

    @property
    def reranker(self) -> Reranker:
        if self._reranker is None:
            self._reranker = get_reranker(self.settings)
        return self._reranker

    # ------------------------------------------------------------------ indexing
    def index_bid(self, bid_dir: Union[str, Path], force: bool = False, bid_id: Optional[str] = None) -> dict:
        """Parse + chunk + embed one bid folder. Incremental: other bids are untouched and an
        unchanged folder (same content hash) is skipped unless force=True."""
        bid_dir = Path(bid_dir)
        bid_id = bid_id or bid_dir.name
        t0 = time.perf_counter()
        if not bid_dir.is_dir():
            return {"bid_id": bid_id, "status": "error", "n_chunks": 0, "warnings": [f"{bid_dir} is not a folder"]}
        fp = folder_fingerprint(bid_dir)
        prev = self.store.manifest.get(bid_id)
        if prev and prev.get("fingerprint") == fp and not force:
            return {"bid_id": bid_id, "status": "skipped (unchanged)", "n_chunks": prev["n_chunks"],
                    "documents": prev.get("documents", []), "warnings": []}
        res = ingest_folder(bid_dir, self.settings, bid_id)
        if not res.chunks:
            return {"bid_id": bid_id, "status": "empty", "n_chunks": 0, "documents": [], "warnings": res.warnings}
        vectors = self.embedder.embed_documents([c.embed_text() for c in res.chunks])
        docs = [{"file": d.file_name, "doc_type": d.doc_type, "addendum_number": d.addendum_number,
                 "pages": len(d.pages), "date": d.doc_date} for d in res.documents]
        meta = {"fingerprint": fp, "n_chunks": len(res.chunks), "documents": docs,
                "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.store.replace_bid(bid_id, [c.to_dict() for c in res.chunks], vectors, meta)
        return {"bid_id": bid_id, "status": "indexed", "n_chunks": len(res.chunks), "documents": docs,
                "warnings": res.warnings, "seconds": round(time.perf_counter() - t0, 1)}

    def remove_bid(self, bid_id: str) -> None:
        self.store.remove_bid(bid_id)

    def refresh_if_stale(self) -> None:
        if self.store.stale():
            self.store.load()

    # ------------------------------------------------------------------ introspection
    def list_bids(self) -> dict:
        self.refresh_if_stale()
        return self.store.manifest

    def list_documents(self, bid_id: str, doc_type: Optional[str] = None) -> list[dict]:
        docs = list(self.list_bids().get(bid_id, {}).get("documents", []))
        if doc_type:
            docs = [d for d in docs if d["doc_type"] == doc_type]
        return sorted(docs, key=lambda d: (d.get("addendum_number") or 999, d["file"]))

    def get_chunks(self, bid_id: str, doc_type: Optional[str] = None, addendum_number: Optional[int] = None,
                   file: Optional[str] = None) -> list[dict]:
        """All chunks matching the filter in reading order (used for short docs such as addenda)."""
        self.refresh_if_stale()
        m = self.store.mask(bid_id=bid_id, doc_type=doc_type, addendum_number=addendum_number)
        out = [c for c, keep in zip(self.store.chunks, m) if keep and (file is None or c["file_name"] == file)]
        return sorted(out, key=lambda c: (c["file_name"], c["page_number"],
                                          int(c["chunk_id"].rsplit("::c", 1)[-1])))

    # ------------------------------------------------------------------ retrieval
    def _ensure_bm25(self) -> Optional[BM25Okapi]:
        key = (len(self.store.chunks), self.store._mtime)
        if self._bm25 is None or key != self._bm25_key:
            corpus = [tokenize(f"{c['doc_type']} {c.get('addendum_number') or ''} {c['file_name']} "
                               f"{c.get('section', '')} {c['text']}") for c in self.store.chunks]
            self._bm25 = BM25Okapi(corpus) if corpus else None
            self._bm25_key = key
        return self._bm25

    def search(self, query: str, *, bid_id: Union[str, Sequence[str], None] = None,
               doc_type: Union[str, Sequence[str], None] = None, addendum_number: Optional[int] = None,
               file_name: Optional[str] = None, top_k: int = 5, mode: str = "hybrid_rerank",
               expand: bool = True, extra_queries: Optional[Sequence[str]] = None,
               candidates: Optional[int] = None) -> list[SearchHit]:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.refresh_if_stale()
        if not self.store.chunks or not query.strip():
            return []
        mask = self.store.mask(bid_id, doc_type, addendum_number, file_name)
        if not mask.any():
            return []

        queries: list[str] = []
        for q in [query, *(extra_queries or [])]:
            queries.extend(expand_query(q) if expand else [q])
        queries = list(dict.fromkeys(q for q in queries if q.strip()))
        n_cand = candidates or max(top_k * 4, self.settings.rerank_candidates)

        use_dense = mode in ("vector", "hybrid", "hybrid_rerank")
        use_bm25 = mode in ("bm25", "hybrid", "hybrid_rerank")
        rankings: list[list[int]] = []
        dense_s: dict[int, float] = {}
        bm25_s: dict[int, float] = {}

        bm25 = self._ensure_bm25() if use_bm25 else None
        for q in queries:
            if use_dense:
                try:
                    sc = np.where(mask, self.store.vectors @ self.embedder.embed_query(q), -np.inf)
                    order = [int(i) for i in np.argsort(-sc)[:n_cand] if np.isfinite(sc[i])]
                    rankings.append(order)
                    for i in order:
                        dense_s[i] = max(dense_s.get(i, -1.0), float(sc[i]))
                except Exception as e:  # noqa: BLE001 – degrade to keyword search instead of failing
                    log.warning("dense search unavailable (%s); falling back to BM25", e)
                    if bm25 is None:
                        bm25 = self._ensure_bm25()
                    use_bm25 = True
            if use_bm25 and bm25 is not None:
                toks = tokenize(q)
                if toks:
                    sc = np.where(mask, np.asarray(bm25.get_scores(toks), dtype=float), -np.inf)
                    order = [int(i) for i in np.argsort(-sc)[:n_cand] if np.isfinite(sc[i]) and sc[i] > 0]
                    rankings.append(order)
                    for i in order:
                        bm25_s[i] = max(bm25_s.get(i, 0.0), float(sc[i]))

        fused = rrf_fuse(rankings, self.settings.rrf_k)[:n_cand]
        hits = [SearchHit(self.store.chunks[i], s, {k: v for k, v in
                          (("dense", dense_s.get(i)), ("bm25", bm25_s.get(i)), ("rrf", s)) if v is not None})
                for i, s in fused]
        if mode == "hybrid_rerank" and len(hits) > 1:
            hits = self.reranker.rerank(query, hits[: self.settings.rerank_candidates], top_k)
        return hits[:top_k]

