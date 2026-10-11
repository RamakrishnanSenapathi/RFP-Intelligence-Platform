"""Addendum Reconciliation Agent: reads each addendum (in numeric order) and applies the latest valid
change to the draft fields, producing a change log. Changes are only applied when their supporting quote
is verifiably present in the addendum text."""
from __future__ import annotations

import re

from observability import traced, record_tool
from utils import quote_supported, truncate
from .runtime import get_runtime
from .schemas import AddendumAnalysis

SYSTEM = """You are an addendum-reconciliation specialist for public-procurement bids.
You receive the text of ONE addendum and the current values of the bid fields. List every change, clarification
or answer the addendum makes (dates, quantities, specifications, forms, requirements, contacts, Q&A).
For each item: map it to exactly one field name from the provided list when it changes/sets that field's value
(otherwise field=null), give old_value (if known), new_value (normalised like the field's current value; Due Date as
'YYYY-MM-DD HH:MM TZ'), a SHORT VERBATIM evidence_quote (<=200 chars) copied from the addendum, and the page number
shown in the [Page N] markers. Never invent changes; if the addendum only acknowledges receipt, return no changes."""

MAX_CHARS = 30000


def _assemble(chunks: list[dict]) -> tuple[str, dict[int, str]]:
    pages: dict[int, list[str]] = {}
    for c in chunks:
        pages.setdefault(c["page_number"], []).append(c["text"])
    page_text = {p: "\n\n".join(t) for p, t in pages.items()}
    text = "\n\n".join(f"[Page {p}]\n{t}" for p, t in sorted(page_text.items()))
    return text[:MAX_CHARS], page_text


@traced("addendum_reconciliation")
def reconcile_node(state: dict) -> dict:
    rt = get_runtime()
    bid_id = state["bid_id"]
    drafts = {k: dict(v) for k, v in state.get("drafts", {}).items()}
    todo = [f for f, d in drafts.items() if not d.get("reconciled")]
    changes_log: list[dict] = []
    if todo:
        for doc in rt.engine.list_documents(bid_id, doc_type="addendum"):
            chunks = rt.engine.get_chunks(bid_id, doc_type="addendum", file=doc["file"])
            record_tool("get_chunks", {"bid_id": bid_id, "file": doc["file"]}, f"{len(chunks)} chunks")
            text, page_text = _assemble(chunks)
            if len(text.strip()) < 40:
                changes_log.append({"addendum_number": doc.get("addendum_number"), "file": doc["file"], "field": None,
                                    "change_summary": "Addendum has no readable text (scanned?); not reconciled",
                                    "applied": False})
                continue
            current = {f: drafts[f]["value"] for f in todo}
            prompt = (f"Addendum file: {doc['file']} (addendum number {doc.get('addendum_number')})\n\n"
                      f"CURRENT FIELD VALUES:\n" + "\n".join(f'- "{f}": {v!r}' for f, v in current.items()) +
                      f"\n\nADDENDUM TEXT:\n{text}")
            analysis = rt.llm.generate_json(prompt, AddendumAnalysis, system=SYSTEM)
            lookup = {f.lower(): f for f in todo}
            for ch in analysis.changes:
                field = lookup.get((ch.field or "").strip().lower())
                page = ch.page
                if ch.evidence_quote and (page is None or page not in page_text):
                    page = next((p for p, t in page_text.items() if quote_supported(ch.evidence_quote, t)), page)
                verified = bool(ch.evidence_quote) and quote_supported(ch.evidence_quote, "\n".join(page_text.values()))
                entry = {"addendum_number": doc.get("addendum_number"), "file": doc["file"], "field": field,
                         "change_summary": ch.change_summary, "old_value": ch.old_value, "new_value": ch.new_value,
                         "page": page, "quote": truncate(ch.evidence_quote, 300), "applied": False}
                if field and ch.new_value and verified:
                    d = drafts[field]
                    prev = d.get("value")
                    if prev != ch.new_value:
                        n = doc.get("addendum_number")
                        src = {"file": doc["file"], "page": page, "chunk_id": None, "doc_type": "addendum",
                               "addendum_number": n, "quote": truncate(ch.evidence_quote, 300), "verified": True}
                        d["value"] = ch.new_value
                        d["sources"] = [src] + [s for s in d.get("sources", []) if s.get("file") != doc["file"]]
                        d["notes"] = (f"Updated by Addendum {n} (previous: {prev}). " + (d.get("notes") or "")).strip()
                        d["confidence"] = max(float(d.get("confidence", 0)), 0.85)
                        entry["old_value"] = entry["old_value"] or prev
                        entry["applied"] = True
                elif field and ch.new_value and not verified:
                    entry["change_summary"] += " [not applied: supporting quote not found in addendum text]"
                changes_log.append(entry)
    for d in drafts.values():
        d["reconciled"] = True
    return {"drafts": drafts, "addendum_changes": changes_log}
