"""Shared LangGraph state (the 'memory object' all agents read from / write to)."""
import operator
from typing import Annotated, TypedDict


def merge_dicts(a, b):
    out = dict(a or {})
    out.update(b or {})
    return out


class BidState(TypedDict, total=False):
    # request
    run_id: str
    mode: str                 # "extract" | "ask"
    bid_id: str
    bid_dir: str
    question: str
    force_reindex: bool
    # orchestrator
    plan: list
    retries: dict             # field -> number of re-extractions so far
    retry_fields: list
    feedback: dict            # field -> validator feedback for next attempt
    # ingestion
    indexed_chunks: int
    ingestion: dict
    # working memory (parallel-safe reducers)
    evidence: Annotated[dict, merge_dicts]      # chunk_id -> passage info
    drafts: Annotated[dict, merge_dicts]        # field -> draft record
    addendum_changes: Annotated[list, operator.add]
    # validation
    validation: dict
    failed_fields: list
    # outputs
    final: dict
    answer: dict
    errors: Annotated[list, operator.add]
