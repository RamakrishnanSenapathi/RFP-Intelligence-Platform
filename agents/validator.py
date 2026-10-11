"""Validator / Critic Agent: deterministic checks (citation, quote verification, formats) + an LLM critic that
judges whether each value is supported by its cited evidence. Failed fields go back to the Orchestrator."""
from __future__ import annotations

import re

from observability import traced
from utils import truncate
from .fields import FIELD_BY_NAME
from .runtime import get_runtime
from .schemas import ValidationBatch

SYSTEM = """You are a strict QA critic for bid-data extraction. For each field you get the extracted value and the
cited source passages. Verdict:
- supported: the value is directly stated in (or trivially derived from) the passages
- partial: some parts of the value are not backed by the passages
- unsupported: the value is contradicted by, or absent from, the passages (possible hallucination)
Also flag a base-document value that a listed addendum change has clearly superseded. confidence 0.0-1.0.
Keep 'issue' short and actionable (what to look for when re-extracting)."""

BATCH = 10
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def format_issue(field: str, value: str) -> str:
    if field == "Due Date" and not ISO_DATE.search(value):
        return "Due Date must be normalised as 'YYYY-MM-DD HH:MM TZ' (date missing or not in ISO form)"
    if field == "contact_info" and not (EMAIL.search(value) or re.search(r"\d{3}[^\d]{0,3}\d{3}[^\d]{0,3}\d{4}", value)):
        return "contact_info should contain an email or phone number"
    return ""


@traced("validator")
def validate_node(state: dict) -> dict:
    rt = get_runtime()
    drafts = state.get("drafts", {})
    evidence = state.get("evidence", {})
    changes = state.get("addendum_changes", [])
    report: dict[str, dict] = {}
    to_judge: list[str] = []

    for name in FIELD_BY_NAME:
        d = drafts.get(name)
        if d is None:
            report[name] = {"status": "failed", "confidence": 0.0, "issues": ["no draft produced (extraction error)"]}
            continue
        if d.get("value") is None:
            report[name] = {"status": "not_found", "confidence": 0.8, "issues": []}
            continue
        issues = []
        srcs = d.get("sources", [])
        if not srcs:
            issues.append("value has no citation")
        elif not any(s.get("verified") for s in srcs):
            issues.append("no cited quote could be verified in the cited passage")
        fi = format_issue(name, d["value"])
        if fi:
            issues.append(fi)
        if issues:
            report[name] = {"status": "failed", "confidence": 0.3, "issues": issues}
        else:
            to_judge.append(name)

    change_lines = [f"- Addendum {c.get('addendum_number')}: {c.get('field')} -> {c.get('new_value')} ({truncate(c.get('change_summary',''), 120)})"
                    for c in changes if c.get("field")][:20]
    for i in range(0, len(to_judge), BATCH):
        batch = to_judge[i : i + BATCH]
        blocks = []
        for name in batch:
            d = drafts[name]
            cited = []
            for s in d["sources"][:3]:
                ev = evidence.get(s.get("chunk_id") or "")
                body = ev["text"] if ev else s.get("quote", "")
                cited.append(f'  * {s["file"]} p{s["page"]}: {truncate(body, 1100)}')
            blocks.append(f'FIELD "{name}"\n  value: {d["value"]!r}\n  cited passages:\n' + "\n".join(cited))
        prompt = ("Judge each field.\n\n" + "\n\n".join(blocks) +
                  ("\n\nKnown addendum changes:\n" + "\n".join(change_lines) if change_lines else ""))
        try:
            res = rt.llm.generate_json(prompt, ValidationBatch, system=SYSTEM)
            verdicts = {v.field.strip().lower(): v for v in res.verdicts}
        except Exception as e:  # noqa: BLE001 – critic unavailable: keep deterministic result, lower confidence
            verdicts = {}
            for name in batch:
                report[name] = {"status": "passed", "confidence": min(0.6, float(drafts[name].get("confidence", 0.5))),
                                "issues": [f"LLM critic unavailable: {e!r}"[:160]]}
        for name in batch:
            v = verdicts.get(name.lower())
            if v is None:
                report.setdefault(name, {"status": "passed", "confidence": min(0.6, float(drafts[name].get("confidence", 0.5))),
                                         "issues": ["critic returned no verdict"]})
                continue
            bad = v.verdict == "unsupported" or (v.verdict == "partial" and v.confidence < 0.5)
            report[name] = {"status": "failed" if bad else "passed",
                            "confidence": max(0.0, min(1.0, float(v.confidence))),
                            "issues": [v.issue] if v.issue else []}

    failed = [n for n, r in report.items() if r["status"] == "failed"]
    counts = {"passed": sum(r["status"] == "passed" for r in report.values()),
              "failed": len(failed), "not_found": sum(r["status"] == "not_found" for r in report.values())}
    return {"validation": {"fields": report, "counts": counts}, "failed_fields": failed}
