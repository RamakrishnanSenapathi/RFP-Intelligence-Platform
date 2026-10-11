"""Streamlit UI:  streamlit run app.py   (or: python main.py ui)"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import streamlit as st

from config import get_settings

logging.basicConfig(level=logging.WARNING)
st.set_page_config(page_title="RFP Intelligence Platform", page_icon="📑", layout="wide")
settings = get_settings()

if not settings.google_api_key and settings.embedder_backend != "hash":
    st.error("GOOGLE_API_KEY is missing. Copy `.env.example` to `.env`, add your Google AI Studio key and restart.")
    st.stop()


@st.cache_resource
def runtime():
    from agents.runtime import get_runtime
    return get_runtime()


rt = runtime()
engine = rt.engine
settings.bids_dir.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ sidebar: bids
with st.sidebar:
    st.title("📑 RFP Intelligence")
    st.caption(f"LLM: `{settings.llm_model}` · Embeddings: `{settings.embed_model}`")
    folders = sorted(p.name for p in settings.bids_dir.iterdir() if p.is_dir())
    st.subheader("Bid folders")
    st.caption(f"`{settings.bids_dir}`")
    indexed = engine.list_bids()
    for f in folders:
        st.write(("✅ " if f in indexed else "⬜ ") + f)
    if st.button("Index / refresh all bid folders", use_container_width=True, disabled=not folders):
        with st.spinner("Parsing, chunking and embedding (unchanged folders are skipped)…"):
            for f in folders:
                r = engine.index_bid(settings.bids_dir / f)
                st.write(f"**{f}**: {r['status']} ({r['n_chunks']} chunks)")
                for w in r.get("warnings", []):
                    st.warning(w)
        st.rerun()
    st.subheader("Add a new bid")
    new_name = st.text_input("Bid folder name", placeholder="Bid3")
    uploads = st.file_uploader("Upload HTML / PDF files", accept_multiple_files=True, type=["pdf", "html", "htm", "txt"])
    if st.button("Save & index", disabled=not (new_name and uploads), use_container_width=True):
        target = settings.bids_dir / new_name.strip()
        target.mkdir(parents=True, exist_ok=True)
        for u in uploads:
            (target / u.name).write_bytes(u.getbuffer())
        with st.spinner("Indexing…"):
            st.json(engine.index_bid(target))
        st.rerun()

tab_search, tab_chat, tab_extract, tab_eval = st.tabs(["🔎 Search", "💬 Chat", "🧾 Extract bid", "📊 Retrieval eval"])
bid_options = list(engine.list_bids())

# ------------------------------------------------------------------ search
with tab_search:
    c1, c2, c3, c4 = st.columns([3, 1.3, 1.3, 1.3])
    q = c1.text_input("Search query", placeholder="e.g. bid bond amount, or a part number like 210-BMKX")
    bid = c2.selectbox("Bid", ["(all)"] + bid_options)
    dtype = c3.selectbox("Document type", ["(all)", "bid_page", "rfp", "addendum", "specs", "affidavit"])
    mode = c4.selectbox("Mode", ["hybrid_rerank", "hybrid", "vector", "bm25"])
    top_k = st.slider("Top K", 1, 15, 5)
    if q:
        with st.spinner("Searching…"):
            hits = engine.search(q, bid_id=None if bid == "(all)" else bid, doc_type=None if dtype == "(all)" else dtype,
                                 top_k=top_k, mode=mode)
        if not hits:
            st.info("No results. Have you indexed a bid?")
        for i, h in enumerate(hits, 1):
            c = h.citation()
            tag = c["doc_type"] + (f" #{c['addendum_number']}" if c["addendum_number"] else "")
            with st.expander(f"#{i} · {c['bid_id']} · {c['file']} · page {c['page']} · {tag}", expanded=i <= 2):
                st.caption("scores: " + ", ".join(f"{k}={v:.3f}" for k, v in h.scores.items()))
                st.markdown(c["text"])

# ------------------------------------------------------------------ chat
with tab_chat:
    cbid = st.selectbox("Restrict to bid (optional)", ["(all)"] + bid_options, key="chat_bid")
    if "messages" not in st.session_state:
        st.session_state.messages = []
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            for c in m.get("citations", []):
                with st.expander(f"[{c['id']}] {c['bid_id']} · {c['file']} · p{c['page']}"):
                    st.write(c["passage"])
    if prompt := st.chat_input("Ask about the bids… e.g. What is the submission deadline for Bid1 after all addendums?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Agents are working…"):
                from agents.runner import run_question
                ans = run_question(prompt, None if cbid == "(all)" else cbid)
            st.markdown(ans["answer"])
            for c in ans.get("citations", []):
                with st.expander(f"[{c['id']}] {c['bid_id']} · {c['file']} · p{c['page']}"):
                    st.write(c["passage"])
        st.session_state.messages.append({"role": "assistant", "content": ans["answer"], "citations": ans.get("citations", [])})

# ------------------------------------------------------------------ extraction
with tab_extract:
    ebid = st.selectbox("Bid to extract", folders, key="ext_bid") if folders else None
    force = st.checkbox("Force re-index first")
    if ebid and st.button("Run extraction (multi-agent)", type="primary"):
        with st.spinner("Orchestrator → ingestion → parallel extraction → reconciliation → validation… (takes a few minutes on free-tier rate limits)"):
            from agents.runner import run_extraction
            st.session_state["final"] = run_extraction(str(settings.bids_dir / ebid), force_reindex=force)
    final = st.session_state.get("final")
    if final:
        v = final["validation"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Passed", v["passed"]); m2.metric("Failed", v["failed"]); m3.metric("Not found", v["not_found"])
        rows = [{"Field": k, "Value": f["value"], "Confidence": f["confidence"], "Status": f.get("status"),
                 "Sources": "; ".join(f"{s['file']} p{s['page']}" for s in f["sources"]), "Notes": f["notes"]}
                for k, f in final["fields"].items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if final.get("addendum_changes"):
            st.subheader("Addendum change log")
            st.dataframe(pd.DataFrame(final["addendum_changes"]), use_container_width=True, hide_index=True)
        if final.get("errors"):
            st.warning("\n".join(final["errors"]))
        st.download_button("Download JSON", json.dumps(final, indent=2, ensure_ascii=False),
                           file_name=f"{final['bid_id']}.json", mime="application/json")
        with st.expander("Agent trace summary"):
            st.json(final.get("trace_summary", {}))
            tp = settings.output_dir / f"{final['bid_id']}_trace.md"
            if tp.exists():
                st.markdown(tp.read_text(encoding="utf-8"))

# ------------------------------------------------------------------ eval
with tab_eval:
    st.write("Runs `eval/eval_set.json` against vector-only, BM25-only, hybrid and hybrid + re-ranker.")
    if st.button("Run retrieval evaluation"):
        from eval.run_eval import run_eval
        with st.spinner("Evaluating…"):
            res = run_eval()
        st.dataframe(pd.DataFrame({k: {m: v[m] for m in ("Recall@1", "Recall@3", "Recall@5", "MRR")} for k, v in res.items()}).T)
