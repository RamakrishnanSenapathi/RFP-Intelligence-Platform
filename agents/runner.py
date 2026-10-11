"""High-level entry points used by the CLI, API and Streamlit UI."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from observability import get_tracer
from .graph import build_graph
from .runtime import get_runtime


def _run_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def run_extraction(bid_dir: str, out_dir: Optional[Path] = None, force_reindex: bool = False) -> dict:
    """Extraction mode: bid folder -> structured JSON record (+ trace + readable report)."""
    rt = get_runtime()
    bid_dir = Path(bid_dir).resolve()
    run_id = _run_id(f"extract-{bid_dir.name}")
    state = get_graph_result({"run_id": run_id, "mode": "extract", "bid_dir": str(bid_dir), "bid_id": bid_dir.name,
                              "force_reindex": force_reindex})
    final = state.get("final") or {"bid_id": bid_dir.name, "fields": {}, "addendum_changes": [],
                                   "validation": {"passed": 0, "failed": 0, "not_found": 0},
                                   "errors": state.get("errors", []) or ["pipeline produced no output"]}
    tracer = get_tracer(run_id)
    final["run_id"] = run_id
    final["trace_summary"] = tracer.summary()
    out_dir = Path(out_dir or rt.settings.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{final['bid_id']}.json").write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / f"{final['bid_id']}_trace.md").write_text(tracer.to_markdown(), encoding="utf-8")
    (out_dir / f"{final['bid_id']}_report.md").write_text(to_markdown_report(final), encoding="utf-8")
    return final


def run_question(question: str, bid_id: Optional[str] = None) -> dict:
    """Q&A mode: cited natural-language answer."""
    run_id = _run_id("ask")
    state = get_graph_result({"run_id": run_id, "mode": "ask", "question": question, "bid_id": bid_id or ""})
    ans = state.get("answer") or {"question": question, "answer": "Not found in documents.", "found": False,
                                  "citations": [], "confidence": 0.0, "errors": state.get("errors", [])}
    ans["run_id"] = run_id
    return ans


def get_graph_result(initial: dict) -> dict:
    try:
        return build_graph().invoke(initial, config={"recursion_limit": 60, "max_concurrency": 3})
    except Exception as e:  # noqa: BLE001 – never crash the caller
        return {"errors": [f"graph failure: {e!r}"]}


def to_markdown_report(final: dict) -> str:
    lines = [f"# Bid report: {final['bid_id']}", ""]
    v = final.get("validation", {})
    lines.append(f"**Validation:** {v.get('passed', 0)} passed · {v.get('failed', 0)} failed · {v.get('not_found', 0)} not found\n")
    lines.append("| Field | Value | Conf. | Sources |\n|---|---|---|---|")
    for k, f in final.get("fields", {}).items():
        val = str(f["value"]).replace("\n", " ").replace("|", "/") if f["value"] is not None else "_not found_"
        src = "; ".join(f"{s['file']} p{s['page']}" for s in f.get("sources", []))
        lines.append(f"| {k} | {val[:300]} | {f['confidence']} | {src} |")
    if final.get("addendum_changes"):
        lines += ["", "## Addendum changes"]
        for c in final["addendum_changes"]:
            lines.append(f"- Addendum {c.get('addendum_number')} – {c.get('field') or 'general'}: {c.get('change_summary')}"
                         f" (applied={c.get('applied')})")
    if final.get("errors"):
        lines += ["", "## Errors"] + [f"- {e}" for e in final["errors"]]
    return "\n".join(lines)
