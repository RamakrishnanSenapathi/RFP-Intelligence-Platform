import dataclasses

import pytest

from config import Settings
from search.embeddings import HashEmbedder
from search.engine import SearchEngine, rrf_fuse
from search.query import expand_query, tokenize
from search.rerankers import NoReranker


def _mk_bid(root, name, files):
    d = root / name
    d.mkdir(parents=True)
    for fn, body in files.items():
        (d / fn).write_text(body, encoding="utf-8")
    return d


@pytest.fixture()
def engine(tmp_path):
    s = dataclasses.replace(Settings.from_env(), data_dir=tmp_path / "data", embedder_backend="hash",
                            reranker_backend="none", chunk_min_chars=20)
    e = SearchEngine(s, embedder=HashEmbedder(), reranker=NoReranker())
    bids = tmp_path / "bids"
    b1 = _mk_bid(bids, "Bid1", {
        "RFP Final.txt": "1. SUBMISSION\n\nBids are due by March 14, 2025 at 2:00 PM EST via the online portal.\n\n"
                          "2. BONDS\n\nA bid bond of 5 percent is required.",
        "Addendum 2.txt": "ADDENDUM NO. 2\n\nThe due date is extended to April 10, 2025 at 2:00 PM EST."})
    b2 = _mk_bid(bids, "Bid2", {
        "Dell Laptop Specs.txt": "Model Latitude-5550 part number 210-BMKX. Processor Intel Core Ultra 7, 16GB RAM, 512GB SSD.",
        "Mercury Affidavit.txt": "AFFIDAVIT: The bidder certifies compliance with Mercury regulations."})
    e.b1, e.b2 = b1, b2
    e.index_bid(b1)
    e.index_bid(b2)
    return e


def test_tokenize_keeps_ids():
    t = tokenize("Bid JA-207652 model 210-BMKX")
    assert "ja-207652" in t and "207652" in t and "210-bmkx" in t


def test_expand_query():
    assert len(expand_query("What is the deadline?")) == 2
    assert "due date" in expand_query("deadline")[1]
    assert expand_query("xyzzy") == ["xyzzy"]


def test_rrf():
    fused = dict(rrf_fuse([[1, 2, 3], [3, 1]], k=60))
    assert fused[1] > fused[2] and fused[3] > fused[2]


def test_keyword_exact_match(engine):
    hits = engine.search("210-BMKX", mode="bm25", top_k=3)
    assert hits and hits[0].chunk["bid_id"] == "Bid2" and "210-BMKX" in hits[0].chunk["text"]


def test_hybrid_and_citations(engine):
    hits = engine.search("bid due date deadline", bid_id="Bid1", top_k=3, mode="hybrid")
    assert hits
    c = hits[0].citation()
    assert c["file"] and c["page"] >= 1 and c["text"]


def test_metadata_filters(engine):
    assert {h.chunk["bid_id"] for h in engine.search("due date", bid_id="Bid1", top_k=10, mode="hybrid")} == {"Bid1"}
    hits = engine.search("due date", bid_id="Bid1", doc_type="addendum", top_k=10, mode="bm25")
    assert hits and all(h.chunk["doc_type"] == "addendum" and h.chunk["addendum_number"] == 2 for h in hits)
    assert engine.search("due date", bid_id="Bid1", addendum_number=9, mode="bm25") == []
    assert all(h.chunk["doc_type"] == "affidavit" for h in engine.search("certifies", doc_type="affidavit", mode="hybrid"))


def test_incremental_indexing(engine, tmp_path):
    before = len(engine.store.chunks)
    again = engine.index_bid(engine.b1)
    assert again["status"].startswith("skipped")
    _mk_bid(tmp_path / "bids", "Bid3", {"RFP.txt": "Bid 3 requires installation and imaging of 50 tablets."})
    res = engine.index_bid(tmp_path / "bids" / "Bid3")
    assert res["status"] == "indexed"
    assert len(engine.store.chunks) == before + res["n_chunks"]
    assert set(engine.list_bids()) == {"Bid1", "Bid2", "Bid3"}
    # persisted: a fresh engine sees all three bids without re-indexing
    e2 = SearchEngine(engine.settings, embedder=HashEmbedder(), reranker=NoReranker())
    assert set(e2.list_bids()) == {"Bid1", "Bid2", "Bid3"}
    assert e2.search("imaging tablets", mode="hybrid")[0].chunk["bid_id"] == "Bid3"


def test_get_chunks_and_documents(engine):
    chunks = engine.get_chunks("Bid1", doc_type="addendum", addendum_number=2)
    assert chunks and "extended" in chunks[0]["text"]
    assert engine.list_documents("Bid1", doc_type="addendum")[0]["addendum_number"] == 2
