"""Retrieval evaluation: Recall@k and MRR for vector / bm25 / hybrid / hybrid+rerank."""
from __future__ import annotations

import json
from pathlib import Path

from agents.runtime import get_runtime

CONFIGS = [("vector-only", "vector"), ("bm25-only", "bm25"), ("hybrid (RRF)", "hybrid"), ("hybrid + LLM re-ranker", "hybrid_rerank")]
KS = (1, 3, 5)


def is_relevant(chunk: dict, spec: dict) -> bool:
    text = chunk["text"].lower()
    if spec.get("file_contains") and spec["file_contains"].lower() not in chunk["file_name"].lower():
        return False
    if spec.get("doc_type") and chunk["doc_type"] != spec["doc_type"]:
        return False
    if spec.get("text_any") and not any(s.lower() in text for s in spec["text_any"]):
        return False
    return all(s.lower() in text for s in spec.get("text_all", []))


def evaluate(engine, questions: list[dict], mode: str, k_max: int = 10) -> dict:
    recall = {k: 0.0 for k in KS}
    mrr = 0.0
    per_q = []
    for q in questions:
        hits = engine.search(q["question"], bid_id=q.get("bid_id"), top_k=k_max, mode=mode)
        rank = next((i for i, h in enumerate(hits, 1) if is_relevant(h.chunk, q)), None)
        for k in KS:
            recall[k] += 1.0 if rank and rank <= k else 0.0
        mrr += 1.0 / rank if rank else 0.0
        per_q.append({"id": q["id"], "first_relevant_rank": rank})
    n = max(len(questions), 1)
    return {**{f"Recall@{k}": round(recall[k] / n, 3) for k in KS}, "MRR": round(mrr / n, 3), "per_question": per_q}


def run_eval(eval_path: str = "eval/eval_set.json", out_dir: str = "eval") -> dict:
    rt = get_runtime()
    questions = json.loads(Path(eval_path).read_text(encoding="utf-8"))["questions"]
    results = {}
    for label, mode in CONFIGS:
        print(f"evaluating {label} ...")
        results[label] = evaluate(rt.engine, questions, mode)
    lines = [f"## Retrieval evaluation ({len(questions)} questions)\n",
             "| Configuration | Recall@1 | Recall@3 | Recall@5 | MRR |", "|---|---|---|---|---|"]
    for label, r in results.items():
        lines.append(f"| {label} | {r['Recall@1']} | {r['Recall@3']} | {r['Recall@5']} | {r['MRR']} |")
    table = "\n".join(lines)
    print("\n" + table)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "results.md").write_text(table + "\n", encoding="utf-8")
    (Path(out_dir) / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results
