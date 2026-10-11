"""Extraction Agent(s): one parallel worker per field group (Dates & Logistics, Commercial & Legal,
Product & Specs). Evidence comes only from the Retrieval Agent; every value must carry citations."""
from __future__ import annotations

from collections import OrderedDict

from observability import traced
from utils import quote_supported, truncate
from .fields import FIELD_BY_NAME
from .runtime import get_runtime
from .schemas import GroupExtraction

SYSTEM = """You are a meticulous bid-document data-extraction specialist.
Rules:
1. Use ONLY the numbered evidence passages provided. Never use outside knowledge and never guess.
2. If a field is not stated in the evidence, set value to null (do not write 'not found').
3. Every non-null value needs at least one citation: the evidence id (e.g. "E3") and a SHORT VERBATIM quote
   (<=200 characters) copied exactly from that passage.
4. Addenda override the base RFP. If an addendum passage changes a value, report the NEW value, cite the
   addendum, and mention the original value in notes. When several addenda conflict, the highest number wins.
5. Keep values concise and factual. Join multiple items with '; '. Normalise Due Date as 'YYYY-MM-DD HH:MM TZ'
   when date and time are known (keep the stated time zone); keep the original wording in notes.
6. confidence is 0.0-1.0: 0.9+ only when the passage states the value explicitly.
Return an entry for EVERY requested field."""

NOT_FOUND_STRINGS = {"", "not found", "not found in documents", "n/a", "na", "unknown", "not specified", "not mentioned"}


def _evidence_block(label: str, hit) -> str:
    c = hit.chunk
    tag = c["doc_type"] + (f" #{c['addendum_number']}" if c.get("addendum_number") else "")
    return f'[{label}] file="{c["file_name"]}" | type={tag} | page={c["page_number"]}\n{c["text"]}'


@traced("extraction")
def extract_group_node(payload: dict) -> dict:
    rt = get_runtime()
    bid_id, names = payload["bid_id"], payload["fields"]
    attempt, feedback = payload.get("attempt", 0), payload.get("feedback", {}) or {}
    specs = [FIELD_BY_NAME[n] for n in names]

    pool: "OrderedDict[str, object]" = OrderedDict()
    per_field: dict[str, list[str]] = {}
    for spec in specs:
        hits = rt.retrieval.retrieve_for_field(bid_id, spec, attempt, feedback.get(spec.name, ""))
        ids = []
        for h in hits:
            pool.setdefault(h.chunk["chunk_id"], h)
            ids.append(h.chunk["chunk_id"])
        per_field[spec.name] = ids

    label_of = {cid: f"E{i}" for i, cid in enumerate(pool, 1)}
    by_label = {label_of[cid]: h for cid, h in pool.items()}
    evidence_text = "\n\n".join(_evidence_block(label_of[cid], h) for cid, h in pool.items())
    field_lines = []
    for s in specs:
        likely = ", ".join(label_of[c] for c in per_field[s.name][:6])
        fb = f"  | PREVIOUS ATTEMPT REJECTED: {feedback[s.name]}" if feedback.get(s.name) else ""
        field_lines.append(f'- "{s.name}": {s.description}  (most relevant evidence: {likely or "none"}){fb}')

    if not pool:
        return {"drafts": {s.name: _draft(s.name, None, [], 0.0, "No evidence retrieved", s.group, attempt) for s in specs}}

    prompt = (f"Bid: {bid_id}\n\nFIELDS TO EXTRACT:\n" + "\n".join(field_lines) +
              f"\n\nEVIDENCE PASSAGES:\n{evidence_text}")
    result = rt.llm.generate_json(prompt, GroupExtraction, system=SYSTEM)
    got = {e.field.strip().lower(): e for e in result.entries}

    drafts: dict = {}
    for spec in specs:
        e = got.get(spec.name.lower())
        if e is None:
            drafts[spec.name] = _draft(spec.name, None, [], 0.0, "Extractor returned no entry", spec.group, attempt)
            continue
        value = (e.value or "").strip() or None
        if value and value.lower() in NOT_FOUND_STRINGS:
            value = None
        sources, verified_any = [], False
        for cit in e.citations:
            hit = by_label.get(cit.evidence_id.strip().strip("[]"))
            if hit is None:
                continue
            c = hit.chunk
            ok = bool(cit.quote) and quote_supported(cit.quote, c["text"])
            verified_any |= ok
            sources.append({"file": c["file_name"], "page": c["page_number"], "chunk_id": c["chunk_id"],
                            "doc_type": c["doc_type"], "addendum_number": c.get("addendum_number"),
                            "quote": truncate(cit.quote, 300), "verified": ok})
        conf = max(0.0, min(1.0, float(e.confidence or 0.0)))
        notes = e.notes or ""
        if value and sources and not verified_any:
            conf *= 0.6
            notes = (notes + " [quote not verbatim in cited passage]").strip()
        drafts[spec.name] = _draft(spec.name, value, sources if value else [], conf if value else 0.0, notes, spec.group, attempt)

    evidence = {cid: {"file": h.chunk["file_name"], "page": h.chunk["page_number"], "doc_type": h.chunk["doc_type"],
                      "addendum_number": h.chunk.get("addendum_number"), "text": h.chunk["text"]}
                for cid, h in pool.items()}
    return {"drafts": drafts, "evidence": evidence}


def _draft(field, value, sources, confidence, notes, group, attempt) -> dict:
    return {"field": field, "value": value, "sources": sources, "confidence": round(confidence, 3),
            "notes": notes, "group": group, "attempt": attempt, "reconciled": False}
