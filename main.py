"""CLI.   python main.py --bid ./data/bids/Bid1          (extraction, one command)
        python main.py index ./data/bids/Bid1 | search "q" | ask "q" | qa-log | eval | serve | ui"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

COMMANDS = {"index", "extract", "search", "ask", "qa-log", "eval", "serve", "ui"}


def _p(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0] in ("-h", "--help"):
        argv.insert(0, "extract")  # `python main.py --bid ./Bid1`

    ap = argparse.ArgumentParser(description="RFP Intelligence Platform")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index", help="index (or re-index) a bid folder"); s.add_argument("bid_dir"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("extract", help="extraction mode"); s.add_argument("--bid", required=True); s.add_argument("--out"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("search", help="raw search"); s.add_argument("query"); s.add_argument("--bid_id"); s.add_argument("--doc_type")
    s.add_argument("--addendum", type=int); s.add_argument("--top_k", type=int, default=5)
    s.add_argument("--mode", default="hybrid_rerank", choices=["vector", "bm25", "hybrid", "hybrid_rerank"])
    s = sub.add_parser("ask", help="question answering"); s.add_argument("question"); s.add_argument("--bid_id")
    s = sub.add_parser("qa-log", help="answer a file of questions -> markdown log")
    s.add_argument("--questions", default="eval/sample_questions.txt"); s.add_argument("--out", default="output/sample_qa_log.md")
    s = sub.add_parser("eval", help="retrieval evaluation"); s.add_argument("--set", default="eval/eval_set.json")
    s = sub.add_parser("serve", help="REST API"); s.add_argument("--port", type=int, default=8000)
    sub.add_parser("ui", help="Streamlit UI")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd == "ui":
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(Path(__file__).parent / "app.py")])
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("api.server:app", host="0.0.0.0", port=a.port)
        return 0

    from agents.runtime import get_runtime
    rt = get_runtime()
    if a.cmd == "index":
        _p(rt.engine.index_bid(a.bid_dir, force=a.force))
    elif a.cmd == "extract":
        from agents.runner import run_extraction
        final = run_extraction(a.bid, Path(a.out) if a.out else None, force_reindex=a.force)
        v = final["validation"]
        print(f"\n{final['bid_id']}: passed={v['passed']} failed={v['failed']} not_found={v['not_found']}")
        print(f"JSON  -> {rt.settings.output_dir / (final['bid_id'] + '.json')}")
        print(f"Trace -> {rt.settings.output_dir / (final['bid_id'] + '_trace.md')}")
        if final.get("errors"):
            print("Errors:", *final["errors"], sep="\n  - ")
    elif a.cmd == "search":
        hits = rt.engine.search(a.query, bid_id=a.bid_id, doc_type=a.doc_type, addendum_number=a.addendum, top_k=a.top_k, mode=a.mode)
        for i, h in enumerate(hits, 1):
            c = h.citation()
            print(f"\n#{i} [{c['bid_id']}] {c['file']} p{c['page']} ({c['doc_type']}) score={h.score:.4f}\n{c['text'][:500]}")
    elif a.cmd == "ask":
        from agents.runner import run_question
        ans = run_question(a.question, a.bid_id)
        print(ans["answer"])
        for c in ans.get("citations", []):
            print(f"  [{c['id']}] {c['bid_id']} / {c['file']} p{c['page']}")
    elif a.cmd == "qa-log":
        from agents.runner import run_question
        qs = [q.strip() for q in Path(a.questions).read_text(encoding="utf-8").splitlines() if q.strip() and not q.startswith("#")]
        out = ["# Sample Q&A log\n"]
        for i, q in enumerate(qs, 1):
            ans = run_question(q)
            out.append(f"## Q{i}. {q}\n\n**Answer:** {ans['answer']}\n")
            for c in ans.get("citations", []):
                out.append(f"- [{c['id']}] {c['bid_id']} / {c['file']} (page {c['page']}): \"{c['passage'][:200].strip()}…\"")
            out.append("")
            print(f"answered {i}/{len(qs)}")
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("\n".join(out), encoding="utf-8")
        print("written", a.out)
    elif a.cmd == "eval":
        from eval.run_eval import run_eval
        run_eval(a.set)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
