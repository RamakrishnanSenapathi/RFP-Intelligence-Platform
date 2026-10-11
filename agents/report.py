"""Q&A / Report Agent: assembles the final JSON record + generates the Bid Summary, and answers questions."""
from __future__ import annotations

from typing import Optional

from observability import traced
from utils import truncate
from .fields import FIELD_BY_NAME, OUTPUT_ORDER, SUMMARY_FIELD
from .runtime import get_runtime
from .schemas import Answer, Summary

SUMMARY_SYSTEM = ("You write concise, factual bid summaries for sales teams. 3-6 sentences. Use only the provided "
                  "field values; do not add facts. Mention what is being bought, who issues it, the (post-addendum) "
                  "due date, submission method and key requirements. If a fact is missing, omit it.")


def _clean_sources(sources: list[dict]) -> list[dict]:
    out, seen = [], set()
    for s in sources:
        key = (s.get("file"), s.get("page"))
        if key in seen:
            continue
        seen.add(key)
        item = {"file": s.get("file"), "page": s.get("page")}
        if s.get("quote"):
            item["quote"] = s["quote"]
        out.append(item)
    return out


@traced("report_agent")
def report_node(state: dict) -> dict:
    rt = get_runtime()
    drafts, vfields = state.get("drafts", {}), state.get("validation", {}).get("fields", {})
    retries = state.get("retries", {})
    fields_out: dict = {}
    for name in OUTPUT_ORDER:
        if name == SUMMARY_FIELD:
            continue
        d, v = drafts.get(name), vfields.get(name, {})
        status = v.get("status", "failed")
        if d is None or d.get("value") is None:
            fields_out[name] = {"value": None, "sources": [], "confidence": round(float(v.get("confidence", 0.8)), 2),
                                "notes": "Not found in documents" if status != "failed" else
                                         "Not found in documents (extraction failed: " + "; ".join(v.get("issues", [])) + ")",
                                "status": status}
            continue
        notes = d.get("notes", "")
        conf = min(float(d.get("confidence", 0.0)), float(v.get("confidence", 1.0))) if status == "passed" else \
            min(float(d.get("confidence", 0.0)), 0.4)
        if status == "failed":
            notes = (notes + " | NEEDS REVIEW – validator: " + "; ".join(v.get("issues", []))).strip(" |")
        fields_out[name] = {"value": d["value"], "sources": _clean_sources(d.get("sources", [])),
                            "confidence": round(conf, 2), "notes": notes, "status": status}
        if retries.get(name):
            fields_out[name]["retries"] = retries[name]

    # ---- generated summary
    known = {k: v["value"] for k, v in fields_out.items() if v["value"] is not None}
    try:
        summary = rt.llm.generate_json("Field values:\n" + "\n".join(f"- {k}: {truncate(str(v), 500)}" for k, v in known.items()) +
                                       "\n\nWrite the bid summary.", Summary, system=SUMMARY_SYSTEM).summary.strip()
        s_conf = 0.85
    except Exception:  # noqa: BLE001
        summary = (f"{known.get('Title', 'Bid')} issued by {known.get('company_name', 'an unspecified agency')}. "
                   f"Due {known.get('Due Date', 'date not found')}. Product: {known.get('Product', 'not found')}.")
        s_conf = 0.5
    key_srcs = []
    for k in ("Title", "Due Date", "Product", "company_name", "Bid Number"):
        if k in fields_out and fields_out[k]["sources"]:
            key_srcs.extend(fields_out[k]["sources"][:1])
    fields_out[SUMMARY_FIELD] = {"value": summary, "sources": _clean_sources(key_srcs)[:5], "confidence": s_conf,
                                 "notes": "Generated from the extracted field values", "status": "passed"}
    fields_out = {k: fields_out[k] for k in OUTPUT_ORDER}

    counts = {"passed": 0, "failed": 0, "not_found": 0}
    for f in fields_out.values():
        counts[f["status"]] += 1
    seen, changes = set(), []
    for c in state.get("addendum_changes", []):
        key = (c.get("addendum_number"), c.get("field"), c.get("change_summary"))
        if key not in seen:
            seen.add(key)
            changes.append(c)
    final = {"bid_id": state["bid_id"], "fields": fields_out, "addendum_changes": changes,
             "validation": {**counts, "retries": retries},
             "documents": state.get("ingestion", {}).get("documents", []),
             "errors": state.get("errors", [])}
    return {"final": final}
