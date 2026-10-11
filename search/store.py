"""Persistent on-disk index: chunks.jsonl + vectors.npy + manifest.json.

Exact cosine search over L2-normalised vectors (equivalent to FAISS IndexFlatIP). Chosen because bid
corpora are small (hundreds–low thousands of chunks) and it keeps metadata filtering trivial. Swapping in
FAISS/Qdrant only requires re-implementing `IndexStore.mask` + the dot product in the engine.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np

log = logging.getLogger("rfp.store")


class IndexStore:
    def __init__(self, index_dir: Path):
        self.dir = Path(index_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.chunks: list[dict] = []
        self.vectors = np.zeros((0, 0), dtype="float32")
        self.manifest: dict[str, dict] = {}
        self._mtime = 0.0
        self.load()

    # ---- paths
    @property
    def _chunks_path(self) -> Path:
        return self.dir / "chunks.jsonl"

    @property
    def _vec_path(self) -> Path:
        return self.dir / "vectors.npy"

    @property
    def _manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    def _disk_mtime(self) -> float:
        return self._manifest_path.stat().st_mtime if self._manifest_path.exists() else 0.0

    # ---- persistence
    def load(self) -> None:
        self.chunks, self.manifest = [], {}
        self.vectors = np.zeros((0, 0), dtype="float32")
        if self._manifest_path.exists() and self._chunks_path.exists() and self._vec_path.exists():
            try:
                self.manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
                self.chunks = [json.loads(l) for l in self._chunks_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                self.vectors = np.load(self._vec_path)
                if len(self.chunks) != len(self.vectors):
                    raise ValueError("chunk/vector count mismatch")
            except Exception as e:  # noqa: BLE001
                log.error("index corrupted (%s) – starting empty", e)
                self.chunks, self.manifest = [], {}
                self.vectors = np.zeros((0, 0), dtype="float32")
        self._mtime = self._disk_mtime()

    def stale(self) -> bool:
        return self._disk_mtime() != self._mtime

    def save(self) -> None:
        tmp_c, tmp_m = self._chunks_path.with_suffix(".tmp"), self._manifest_path.with_suffix(".tmp")
        tmp_v = self.dir / "vectors.tmp.npy"
        tmp_c.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in self.chunks), encoding="utf-8")
        np.save(tmp_v, self.vectors)
        tmp_m.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp_c, self._chunks_path)
        os.replace(tmp_v, self._vec_path)
        os.replace(tmp_m, self._manifest_path)  # manifest last => readers see a consistent snapshot
        self._mtime = self._disk_mtime()

    # ---- mutation (incremental: only the touched bid is replaced)
    def replace_bid(self, bid_id: str, chunks: list[dict], vectors: np.ndarray, meta: dict) -> None:
        keep = [i for i, c in enumerate(self.chunks) if c["bid_id"] != bid_id]
        old = self.vectors[keep] if (self.vectors.size and keep) else None
        if old is not None and vectors.size and old.shape[1] != vectors.shape[1]:
            raise ValueError(
                f"Embedding dimension changed ({old.shape[1]} -> {vectors.shape[1]}). "
                "Delete data/index/ and re-index all bids after changing the embedding model.")
        self.chunks = [self.chunks[i] for i in keep] + list(chunks)
        if old is None:
            self.vectors = vectors.astype("float32")
        else:
            self.vectors = np.vstack([old, vectors]).astype("float32")
        self.manifest[bid_id] = meta
        self.save()

    def remove_bid(self, bid_id: str) -> None:
        keep = [i for i, c in enumerate(self.chunks) if c["bid_id"] != bid_id]
        self.vectors = self.vectors[keep] if self.vectors.size else self.vectors
        self.chunks = [self.chunks[i] for i in keep]
        self.manifest.pop(bid_id, None)
        self.save()

    # ---- filtering
    def mask(self, bid_id: Union[str, Sequence[str], None] = None,
             doc_type: Union[str, Sequence[str], None] = None,
             addendum_number: Optional[int] = None,
             file_name: Optional[str] = None) -> np.ndarray:
        def as_set(v):
            if v is None:
                return None
            return {v.lower()} if isinstance(v, str) else {x.lower() for x in v}

        bids, types = as_set(bid_id), as_set(doc_type)
        fn = file_name.lower() if file_name else None
        out = np.zeros(len(self.chunks), dtype=bool)
        for i, c in enumerate(self.chunks):
            if bids and c["bid_id"].lower() not in bids:
                continue
            if types and c["doc_type"].lower() not in types:
                continue
            if addendum_number is not None and c.get("addendum_number") != addendum_number:
                continue
            if fn and fn not in c["file_name"].lower():
                continue
            out[i] = True
        return out
