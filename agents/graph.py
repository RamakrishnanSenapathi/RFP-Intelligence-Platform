"""LangGraph wiring of the multi-agent system.

START -> orchestrator --(extract)--> ingestion --fan-out--> extract_group x3 (parallel)
                                                   -> reconcile -> validate --(failed & retries left)--> retry_planner
                                                                                      ^---- fan-out ----------'
                                                                  `--(done)--> report -> END
         orchestrator --(ask)--> qa -> END
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from .extraction import extract_group_node
from .orchestrator import (fan_out_initial, fan_out_retry, ingestion_node, orchestrator_node,
                           retry_planner_node, route_after_validate, route_mode)
from .qa import qa_node
from .reconcile import reconcile_node
from .report import report_node
from .state import BidState
from .validator import validate_node


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(BidState)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("ingestion", ingestion_node)
    g.add_node("extract_group", extract_group_node)
    g.add_node("reconcile", reconcile_node)
    g.add_node("validate", validate_node)
    g.add_node("retry_planner", retry_planner_node)
    g.add_node("report", report_node)
    g.add_node("qa", qa_node)

    g.add_edge(START, "orchestrator")
    g.add_conditional_edges("orchestrator", route_mode, {"ingestion": "ingestion", "qa": "qa"})
    g.add_conditional_edges("ingestion", fan_out_initial, ["extract_group", END])
    g.add_edge("extract_group", "reconcile")
    g.add_edge("reconcile", "validate")
    g.add_conditional_edges("validate", route_after_validate, {"retry": "retry_planner", "report": "report"})
    g.add_conditional_edges("retry_planner", fan_out_retry, ["extract_group", "report"])
    g.add_edge("report", END)
    g.add_edge("qa", END)
    return g.compile()
