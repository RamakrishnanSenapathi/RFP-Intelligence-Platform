"""Structured tracing: every agent step is logged as one JSON line.

Each record holds agent, input, output, tool calls, LLM calls, tokens and latency.
Traces are written to data/traces/<run_id>.jsonl and can be rendered as Markdown.
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from config import get_settings

log = logging.getLogger("rfp.trace")
_current: contextvars.ContextVar = contextvars.ContextVar("current_step", default=None)
_run_id: contextvars.ContextVar = contextvars.ContextVar("run_id", default="adhoc")


def _summ(obj: Any, limit: int = 700) -> str:
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class StepRecorder:
    def __init__(self, agent: str, input: Any = None):
        self.agent = agent
        self.input = input
        self.output: Any = None
        self.error: Optional[str] = None
        self.tool_calls: list[dict] = []
        self.llm_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def tool(self, name: str, args: Any, result: Any) -> None:
        self.tool_calls.append({"tool": name, "args": _summ(args, 400), "result": _summ(result, 300)})


def record_llm(tokens_in: int, tokens_out: int, model: str = "", latency: float = 0.0) -> None:
    rec = _current.get()
    if rec is not None:
        rec.llm_calls += 1
        rec.tokens_in += int(tokens_in or 0)
        rec.tokens_out += int(tokens_out or 0)


def record_tool(name: str, args: Any, result: Any) -> None:
    rec = _current.get()
    if rec is not None:
        rec.tool(name, args, result)


class Tracer:
    _registry: dict[str, "Tracer"] = {}
    _reg_lock = threading.Lock()

    def __init__(self, run_id: str, trace_dir: Path):
        self.run_id = run_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = trace_dir / f"{run_id}.jsonl"
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def summary(self) -> dict:
        per: dict[str, dict] = {}
        for r in self.read():
            a = per.setdefault(r["agent"], {"steps": 0, "llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0})
            a["steps"] += 1
            a["llm_calls"] += r.get("llm_calls", 0)
            a["tokens_in"] += r.get("tokens_in", 0)
            a["tokens_out"] += r.get("tokens_out", 0)
            a["latency_ms"] += r.get("latency_ms", 0)
        total = {k: sum(v[k] for v in per.values()) for k in ("steps", "llm_calls", "tokens_in", "tokens_out", "latency_ms")}
        return {"per_agent": per, "total": total}

    def to_markdown(self) -> str:
        lines = [f"# Agent trace `{self.run_id}`", ""]
        for i, r in enumerate(self.read(), 1):
            lines += [
                f"## {i}. {r['agent']}  ({r['latency_ms']} ms, {r.get('llm_calls',0)} LLM calls, "
                f"{r.get('tokens_in',0)}+{r.get('tokens_out',0)} tokens)",
                f"- **input:** `{r.get('input')}`",
            ]
            for t in r.get("tool_calls", []):
                lines.append(f"- **tool** `{t['tool']}` args=`{t['args']}` → {t['result']}")
            lines.append(f"- **output:** `{r.get('output')}`")
            if r.get("error"):
                lines.append(f"- **ERROR:** `{r['error']}`")
            lines.append("")
        s = self.summary()["total"]
        lines += ["---", f"**Totals:** {s['steps']} steps, {s['llm_calls']} LLM calls, "
                  f"{s['tokens_in']} input / {s['tokens_out']} output tokens, {s['latency_ms']} ms (sum of step latencies)"]
        return "\n".join(lines)


def get_tracer(run_id: str) -> Tracer:
    with Tracer._reg_lock:
        if run_id not in Tracer._registry:
            Tracer._registry[run_id] = Tracer(run_id, get_settings().trace_dir)
        return Tracer._registry[run_id]


def _emit(rec: StepRecorder, started: float, err: Optional[str]) -> None:
    try:
        get_tracer(_run_id.get()).write({
            "run_id": _run_id.get(),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "agent": rec.agent,
            "input": _summ(rec.input),
            "output": _summ(rec.output),
            "tool_calls": rec.tool_calls,
            "llm_calls": rec.llm_calls,
            "tokens_in": rec.tokens_in,
            "tokens_out": rec.tokens_out,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": rec.error or err,
        })
    except Exception:  # tracing must never break the pipeline
        log.exception("trace write failed")


@contextmanager
def step(agent: str, input: Any = None):
    rec = StepRecorder(agent, input)
    token = _current.set(rec)
    t0 = time.perf_counter()
    err = None
    try:
        yield rec
    except Exception as e:
        err = repr(e)
        raise
    finally:
        _current.reset(token)
        _emit(rec, t0, err)


def _default_input(state: dict) -> dict:
    keys = ("mode", "bid_id", "question", "group", "fields", "attempt", "failed_fields", "feedback")
    return {k: state[k] for k in keys if k in state}


def traced(agent: str) -> Callable:
    """Decorator for LangGraph nodes: traces the step and converts crashes into state errors."""

    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        @functools.wraps(fn)
        def wrapper(state: dict) -> dict:
            rtok = _run_id.set(state.get("run_id", "adhoc"))
            try:
                with step(agent, _default_input(state)) as rec:
                    try:
                        out = fn(state)
                        rec.output = out
                        return out
                    except Exception as e:  # noqa: BLE001 - isolate failures per node
                        log.exception("agent %s failed", agent)
                        rec.error = repr(e)
                        return {"errors": [f"{agent}: {e!r}"]}
            finally:
                _run_id.reset(rtok)

        return wrapper

    return deco
