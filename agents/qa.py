"""Q&A Agent: plans sub-queries (via the Retrieval Agent), then answers strictly from cited evidence."""
from __future__ import annotations

from observability import traced
from .runtime import get_runtime
from .schemas import Answer

SYSTEM = """You answer questions about public-procurement bids (RFPs) using ONLY the numbered evidence passages.
- If the evidence does not contain the answer, set found=false and answer exactly "Not found in documents."
- Addenda override the base RFP. When asked for a final/current value, give the value from the highest-numbered
  addendum that changes it and mention the original value and the addendum number.
- For comparisons, address each bid separately, then summarise the differences.
- Be concise. List the evidence ids you actually used in used_evidence (e.g. ["E1","E4"]). Do not cite ids you did not use.
- confidence 0.0-1.0."""


@traced("qa_agent")
def qa_node(state: dict) -> dict:
    rt = get_runtime()
    q, bid_id = state["question"], state.get("bid_id") or None
    hits, subs = rt.retrieval.retrieve_for_question(q, bid_id)
    base = {"question": q, "sub_queries": [s.model_dump() for s in subs]}
    if not hits:
        return {"answer": {**base, "answer": "Not found in documents.", "found": False, "citations": [], "confidence": 0.0}}
    labelled = {f"E{i}": h for i, h in enumerate(hits, 1)}
    ev = "\n\n".join(
        f'[{lab}] bid={h.chunk["bid_id"]} | file="{h.chunk["file_name"]}" | type={h.chunk["doc_type"]}'
        + (f' #{h.chunk["addendum_number"]}' if h.chunk.get("addendum_number") else "")
        + f' | page={h.chunk["page_number"]}\n{h.chunk["text"]}' for lab, h in labelled.items())
    ans = rt.llm.generate_json(f"QUESTION: {q}\n\nEVIDENCE:\n{ev}", Answer, system=SYSTEM)
    cites, seen = [], set()
    for lab in ans.used_evidence:
        h = labelled.get(lab.strip().strip("[]"))
        if h and lab not in seen:
            seen.add(lab)
            c = h.citation()
            cites.append({"id": lab, "bid_id": c["bid_id"], "file": c["file"], "page": c["page"],
                          "doc_type": c["doc_type"], "addendum_number": c["addendum_number"],
                          "passage": c["text"][:400]})
    found = ans.found and bool(cites)
    text = ans.answer if found else ("Not found in documents." if not ans.found else ans.answer)
    if ans.found and not cites:  # guardrail: an answer without any citation is not accepted
        text, found = "Not found in documents.", False
    return {"answer": {**base, "answer": text, "found": found, "citations": cites,
                       "confidence": round(float(ans.confidence), 2) if found else 0.0}}
