"""Central configuration. Everything comes from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    google_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768
    embedder_backend: str = "gemini"
    reranker_backend: str = "llm"
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    llm_rpm: int = 12
    llm_timeout_s: int = 120
    llm_max_retries: int = 4
    thinking_budget: Optional[int] = None
    chunk_max_chars: int = 1200
    chunk_overlap_chars: int = 150
    chunk_min_chars: int = 150
    rerank_candidates: int = 15
    rrf_k: int = 60
    max_retries: int = 2
    rerank_in_extraction: bool = True
    llm_query_rewrite: bool = True
    data_dir: Path = ROOT / "data"
    output_dir: Path = ROOT / "output"

    # ---- derived paths ----
    @property
    def bids_dir(self) -> Path:
        return self.data_dir / "bids"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def trace_dir(self) -> Path:
        return self.data_dir / "traces"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def from_env(cls) -> "Settings":
        tb = _env("GEMINI_THINKING_BUDGET")
        return cls(
            google_api_key=_env("GOOGLE_API_KEY") or _env("GEMINI_API_KEY"),
            llm_model=_env("GEMINI_MODEL", cls.llm_model),
            embed_model=_env("EMBED_MODEL", cls.embed_model),
            embed_dim=int(_env("EMBED_DIM", str(cls.embed_dim))),
            embedder_backend=_env("EMBEDDER_BACKEND", cls.embedder_backend).lower(),
            reranker_backend=_env("RERANKER_BACKEND", cls.reranker_backend).lower(),
            cross_encoder_model=_env("CROSS_ENCODER_MODEL", cls.cross_encoder_model),
            llm_rpm=int(_env("LLM_RPM", str(cls.llm_rpm))),
            llm_timeout_s=int(_env("LLM_TIMEOUT_S", str(cls.llm_timeout_s))),
            llm_max_retries=int(_env("LLM_MAX_RETRIES", str(cls.llm_max_retries))),
            thinking_budget=int(tb) if tb else None,
            chunk_max_chars=int(_env("CHUNK_MAX_CHARS", str(cls.chunk_max_chars))),
            chunk_overlap_chars=int(_env("CHUNK_OVERLAP_CHARS", str(cls.chunk_overlap_chars))),
            chunk_min_chars=int(_env("CHUNK_MIN_CHARS", str(cls.chunk_min_chars))),
            rerank_candidates=int(_env("RERANK_CANDIDATES", str(cls.rerank_candidates))),
            rrf_k=int(_env("RRF_K", str(cls.rrf_k))),
            max_retries=int(_env("MAX_FIELD_RETRIES", str(cls.max_retries))),
            rerank_in_extraction=_bool("RERANK_IN_EXTRACTION", True),
            llm_query_rewrite=_bool("LLM_QUERY_REWRITE", True),
            data_dir=Path(_env("DATA_DIR", str(ROOT / "data"))).resolve(),
            output_dir=Path(_env("OUTPUT_DIR", str(ROOT / "output"))).resolve(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
