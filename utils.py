"""Small shared helpers (text normalisation, quote verification, JSON cleanup)."""
from __future__ import annotations

import re
from typing import Iterable, TypeVar

T = TypeVar("T")


def norm_words(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def quote_supported(quote: str, text: str, threshold: float = 0.85) -> bool:
    """True if `quote` is (nearly) verbatim in `text`.

    Exact normalised-substring match first; otherwise a bag-of-words overlap
    >= threshold (tolerates PDF line-break / hyphenation noise).
    """
    q, t = norm_words(quote), norm_words(text)
    if not q:
        return False
    if q in t:
        return True
    qt = q.split()
    if len(qt) < 4:
        return False
    tset = set(t.split())
    return sum(1 for w in qt if w in tset) / len(qt) >= threshold


def extract_json_text(raw: str) -> str:
    """Strip markdown fences / chatter and return the JSON substring."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        return s
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    return s[start : end + 1] if end > start else s[start:]


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def dedupe_keep_order(seq: Iterable[T]) -> list[T]:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
