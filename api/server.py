"""REST API:  uvicorn api.server:app --port 8000   (or: python main.py serve)"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agents.runtime import get_runtime
from agents.runner import run_extraction, run_question

app = FastAPI(title="RFP Intelligence API", version="1.0")


class IndexReq(BaseModel):
    bid_dir: str
    force: bool = False


class AskReq(BaseModel):
    question: str
    bid_id: Optional[str] = None


class ExtractReq(BaseModel):
    bid_dir: str
    force: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/bids")
def bids():
    return get_runtime().engine.list_bids()


@app.post("/index")
def index(req: IndexReq):
    if not Path(req.bid_dir).is_dir():
        raise HTTPException(404, f"{req.bid_dir} is not a folder")
    return get_runtime().engine.index_bid(req.bid_dir, force=req.force)


@app.get("/search")
def search(q: str, bid_id: Optional[str] = None, doc_type: Optional[str] = None,
           addendum_number: Optional[int] = None, top_k: int = 5, mode: str = "hybrid_rerank"):
    try:
        hits = get_runtime().engine.search(q, bid_id=bid_id, doc_type=doc_type, addendum_number=addendum_number,
                                           top_k=top_k, mode=mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"query": q, "results": [h.to_dict() for h in hits]}


@app.post("/ask")
def ask(req: AskReq):
    return run_question(req.question, req.bid_id)


@app.post("/extract")
def extract(req: ExtractReq):
    if not Path(req.bid_dir).is_dir():
        raise HTTPException(404, f"{req.bid_dir} is not a folder")
    return run_extraction(req.bid_dir, force_reindex=req.force)
