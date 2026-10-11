"""Orchestrator / Planner + Ingestion Agent nodes and the routing / fan-out functions of the graph."""
from __future__ import annotations

import re
from pathlib import Path

from langgraph.graph import END

try:
    from langgraph.types import Send
except ImportError:  # older langgraph
    from langgraph.constants import Send

from observability import traced
from .fields import GROUPS
from .runtime import get_runtime


@traced("orchestrator")
def orchestrator_node(state: dict) -> dict:
    mode = state.get("mode", "extract")
    if mode == "auto":
        mode = "extract" if (state.get("bid_dir") and not state.get("question")) else "ask"
    if mode == "extract":
        bid_dir = Path(state["bid_dir"])
        bid_id = state.get("bid_id") or bid_dir.name
        plan = ["ingest + index bid folder (incremental)"] + \
               [f"extract group '{g}' ({len(f)} fields) in parallel" for g, f in GROUPS.items()] + \
               ["reconcile addenda in numeric order", "validate every field (deterministic + LLM critic)",
                "re-extract failed fields (max retries)", "generate summary + final JSON"]
        return {"mode": mode, "bid_id": bid_id, "plan": plan, "retries": {}, "feedback": {}}
    return {"mode": "ask", "plan": ["plan sub-queries", "hybrid search + rerank (metadata filtered)",
                                    "answer strictly from cited evidence"]}


def route_mode(state: dict) -> str:
    return "ingestion" if state.get("mode") == "extract" else "qa"


@traced("ingestion_agent")
def ingestion_node(state: dict) -> dict:
    rt = get_runtime()
    res = rt.engine.index_bid(state["bid_dir"], force=state.get("force_reindex", False), bid_id=state.get("bid_id"))
    out = {"indexed_chunks": res["n_chunks"], "ingestion": res}
    if res["n_chunks"] == 0:
        out["errors"] = [f"ingestion: no readable documents in {state['bid_dir']} ({'; '.join(res.get('warnings', []))})"]
    return out


def _sends(state: dict, fields_by_group: dict, attempt: int, feedback: dict):
    return [Send("extract_group", {"run_id": state.get("run_id", "adhoc"), "bid_id": state["bid_id"], "group": g,
                                   "fields": fs, "attempt": attempt, "feedback": feedback})
            for g, fs in fields_by_group.items() if fs]


def fan_out_initial(state: dict):
    """Parallel fan-out: one extraction worker per field group."""
    if state.get("indexed_chunks", 0) == 0:
        return END
    return _sends(state, GROUPS, 0, {})


@traced("orchestrator_retry")
def retry_planner_node(state: dict) -> dict:
    """Feedback loop: decide which failed fields get another extraction attempt."""
    retries = dict(state.get("retries", {}))
    limit = get_runtime().settings.max_retries
    eligible = [f for f in state.get("failed_fields", []) if retries.get(f, 0) < limit]
    feedback = {}
    for f in eligible:
        retries[f] = retries.get(f, 0) + 1
        feedback[f] = "; ".join(state.get("validation", {}).get("fields", {}).get(f, {}).get("issues", []))
    return {"retries": retries, "retry_fields": eligible, "feedback": feedback}


def route_after_validate(state: dict) -> str:
    limit = get_runtime().settings.max_retries
    retries = state.get("retries", {})
    if any(retries.get(f, 0) < limit for f in state.get("failed_fields", [])):
        return "retry"
    return "report"


def fan_out_retry(state: dict):
    from .fields import FIELD_BY_NAME
    by_group: dict[str, list[str]] = {}
    for f in state.get("retry_fields", []):
        by_group.setdefault(FIELD_BY_NAME[f].group, []).append(f)
    attempt = max((state["retries"].get(f, 1) for f in state.get("retry_fields", [])), default=1)
    sends = _sends(state, by_group, attempt, state.get("feedback", {}))
    return sends or "report"
