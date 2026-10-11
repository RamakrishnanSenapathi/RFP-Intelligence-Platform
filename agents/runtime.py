"""Process-wide runtime (settings, search engine, LLM, retrieval agent) shared by all graph nodes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import Settings, get_settings
from search.engine import SearchEngine


@dataclass
class Runtime:
    settings: Settings
    engine: SearchEngine
    llm_getter: object  # callable returning an object with generate_json()

    def __post_init__(self):
        from .retrieval import RetrievalAgent
        self.retrieval = RetrievalAgent(self.engine, self.llm_getter, self.settings)

    @property
    def llm(self):
        return self.llm_getter()


_RT: Optional[Runtime] = None


def get_runtime() -> Runtime:
    global _RT
    if _RT is None:
        from llm import get_llm
        s = get_settings()
        _RT = Runtime(s, SearchEngine(s), get_llm)
    return _RT


def set_runtime(rt: Optional[Runtime]) -> None:
    """Inject a runtime (tests / custom engines)."""
    global _RT
    _RT = rt
