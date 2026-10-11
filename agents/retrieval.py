"""Retrieval / Search Agent: rewrites + expands queries, applies metadata filters, calls the search
engine (hybrid + rerank) and returns cited evidence. All other agents get evidence only through here."""
from __future__ import annotations

import logging
import re
from typing import Optional

from observability import step
from search.engine import SearchEngine, SearchHit
from .fields import FieldSpec
from .schemas import QueryPlan, QueryRewrite, SubQuery

log = logging.getLogger("rfp.retrieval")

_ADD_NUM = re.compile(r"addendum\s*(?:no\.?|number|#)?\s*(\d+)", re.I)


def detect_bid_ids(question: str, bid_ids: list[str]) -> list[str]:
    qn = re.sub(r"[^a-z0-9]", "", question.lower())
    return [b for b in bid_ids if re.sub(r"[^a-z0-9]", "", b.lower()) in qn]


class RetrievalAgent:
    def __init__(self, engine: SearchEngine, llm_getter, settings):
        self.engine, self._llm, self.settings = engine, llm_getter, settings

    # ------------------------------------------------------------- field-level retrieval
    def _rewrite(self, spec: FieldSpec, feedback: str) -> list[str]:
        if not self.settings.llm_query_rewrite:
            return []
        try:
            out = self._llm().generate_json(
                f"We are searching bid documents for the field '{spec.name}' ({spec.description}).\n"
                f"A previous extraction attempt was rejected: {feedback or 'not enough evidence'}.\n"
                "Write 3 alternative search queries (different wording, synonyms, likely section titles).",
                QueryRewrite)
            return [q for q in out.queries if q.strip()][:3]
        except Exception as e:  # noqa: BLE001
            log.warning("query rewrite failed: %s", e)
            return []

    def retrieve_for_field(self, bid_id: str, spec: FieldSpec, attempt: int = 0, feedback: str = "") -> list[SearchHit]:
        queries = list(spec.queries)
        if attempt > 0:
            queries += self._rewrite(spec, feedback)
        top_k = 6 + 2 * attempt
        mode = "hybrid_rerank" if self.settings.rerank_in_extraction else "hybrid"
        with step("retrieval", {"bid_id": bid_id, "field": spec.name, "queries": queries, "attempt": attempt}) as rec:
            hits = self.engine.search(queries[0], extra_queries=queries[1:], bid_id=bid_id, top_k=top_k, mode=mode)
            rec.tool("search", {"mode": mode, "bid_id": bid_id, "top_k": top_k}, f"{len(hits)} hits")
            # always surface the best addendum passages too, so overrides are never missed
            add = self.engine.search(queries[0], extra_queries=queries[1:], bid_id=bid_id, doc_type="addendum",
                                     top_k=2, mode="hybrid")
            rec.tool("search", {"doc_type": "addendum", "mode": "hybrid"}, f"{len(add)} hits")
            seen, merged = set(), []
            for h in hits + add:
                if h.chunk["chunk_id"] not in seen:
                    seen.add(h.chunk["chunk_id"])
                    merged.append(h)
            rec.output = [f"{h.chunk['file_name']}#p{h.chunk['page_number']}" for h in merged]
        return merged

    # ------------------------------------------------------------- question-level retrieval
    def plan(self, question: str, bid_id: Optional[str]) -> list[SubQuery]:
        bids = self.engine.list_bids()
        desc = []
        for b, meta in bids.items():
            files = "; ".join(f"{d['file']} [{d['doc_type']}" + (f" {d['addendum_number']}" if d.get("addendum_number") else "") + "]"
                              for d in meta.get("documents", []))
            desc.append(f"- {b}: {files}")
        fallback_bids = [bid_id] if bid_id else (detect_bid_ids(question, list(bids)) or [None])
        try:
            plan = self._llm().generate_json(
                f"Question: {question}\n\nIndexed bids and files:\n" + "\n".join(desc) + "\n\n"
                "Break the question into 1-5 search sub-queries. Set bid_id when a sub-query concerns one bid "
                "(use null to search all). Set doc_type (bid_page|rfp|addendum|specs|affidavit) only when the "
                "question clearly targets that document type. For comparisons create one sub-query per bid. "
                "If the question asks about the final/current value 'after addendums', add a sub-query with "
                "doc_type='addendum' for that bid.", QueryPlan)
            subs = plan.sub_queries[:6]
        except Exception as e:  # noqa: BLE001
            log.warning("query planning failed (%s); using direct search", e)
            subs = []
        if not subs:
            subs = [SubQuery(query=question, bid_id=b) for b in fallback_bids]
        if bid_id:
            for s in subs:
                s.bid_id = bid_id
        valid = set(bids)
        for s in subs:
            if s.bid_id and s.bid_id not in valid:
                s.bid_id = None
        return subs

    def retrieve_for_question(self, question: str, bid_id: Optional[str] = None, top_k: int = 6,
                              max_evidence: int = 16) -> tuple[list[SearchHit], list[SubQuery]]:
        with step("retrieval", {"question": question, "bid_id": bid_id}) as rec:
            subs = self.plan(question, bid_id)
            rec.tool("plan", {"question": question}, [s.model_dump() for s in subs])
            hits: list[SearchHit] = []
            for s in subs:
                found = self.engine.search(s.query, bid_id=s.bid_id, doc_type=s.doc_type,
                                           addendum_number=s.addendum_number, top_k=top_k, mode="hybrid_rerank")
                rec.tool("search", s.model_dump(), f"{len(found)} hits")
                hits.extend(found)
            # an explicitly named addendum is read in full (addenda are short)
            targets = [bid_id] if bid_id else (detect_bid_ids(question, list(self.engine.list_bids())) or list(self.engine.list_bids()))
            for m in _ADD_NUM.finditer(question):
                n = int(m.group(1))
                for b in targets:
                    for c in self.engine.get_chunks(b, doc_type="addendum", addendum_number=n)[:8]:
                        hits.append(SearchHit(c, 1.0, {"direct": 1.0}))
                        rec.tool("get_chunks", {"bid_id": b, "addendum_number": n}, c["chunk_id"])
            seen, out = set(), []
            for h in hits:
                cid = h.chunk["chunk_id"]
                if cid not in seen:
                    seen.add(cid)
                    out.append(h)
            out = out[:max_evidence]
            rec.output = [f"{h.chunk['bid_id']}/{h.chunk['file_name']}#p{h.chunk['page_number']}" for h in out]
        return out, subs
