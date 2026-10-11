"""Query understanding: tokenisation (keeps model/part numbers intact) and rule-based expansion."""
from __future__ import annotations

import re

STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "for", "is", "are", "be", "on", "by", "with",
             "as", "at", "this", "that", "it", "from", "what", "which", "who", "how", "does", "do", "shall",
             "will", "must", "any", "all"}

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase tokens. 'JA-207652' yields ['ja-207652', 'ja', '207652'] so exact IDs and parts both match."""
    out: list[str] = []
    for t in _TOKEN.findall((text or "").lower()):
        if t in STOPWORDS:
            continue
        out.append(t)
        if re.search(r"[-_./]", t):
            out.extend(p for p in re.split(r"[-_./]", t) if p and p not in STOPWORDS)
    return out


# phrase (lower-case, regex) -> expansion terms
SYNONYMS: list[tuple[str, str]] = [
    (r"deadline|due date|closing|submission date|submit by", "due date, submission deadline, closing date, proposals due, bid opening, no later than"),
    (r"bid bond|bond|security|surety", "bid bond, bid security, surety, performance bond, payment bond, guarantee"),
    (r"warrant", "warranty, extended warranty, years of coverage, on-site service, accidental damage"),
    (r"affidavit|forms?\b|documents? required|certificat", "affidavit, required forms, certification, attachments to submit, W-9, insurance"),
    (r"pre-?bid|pre-?proposal|conference|site visit", "pre-bid meeting, pre-proposal conference, mandatory attendance, questions deadline"),
    (r"payment|invoice|invoicing", "payment terms, net 30, invoice, payment within days, remittance"),
    (r"contact|procurement officer|buyer|purchasing", "contact, procurement officer, buyer, email, phone, questions directed to"),
    (r"deliver|lead time|ship", "delivery date, delivery within days after award, delivery schedule, lead time, FOB"),
    (r"\bterm\b|duration|renew", "contract term, initial term, renewal options, period of performance, extension"),
    (r"install|imaging|deploy|setup|asset tag", "installation, imaging, deployment, setup, asset tagging, configuration services"),
    (r"cooperative|contract vehicle|piggyback|state contract|naspo", "cooperative contract, state contract, piggyback, contract vehicle, master agreement"),
    (r"model|part n|sku|catalog", "model number, part number, SKU, catalog number, manufacturer part"),
    (r"spec|processor|cpu|memory|ram\b|storage|display", "specifications, processor CPU, memory RAM, storage SSD, display screen size, battery, operating system"),
    (r"manufactur|mfg|authorized|reseller|dealer", "manufacturer, authorized reseller, dealer authorization, registered partner"),
    (r"addendum|amend|change", "addendum, amendment, revised, changed, replaced, extended, supersedes"),
]


def expand_query(query: str) -> list[str]:
    """Return [original, expanded] (expanded only when a synonym group matched)."""
    q = (query or "").strip()
    q_low = q.lower()
    extra: list[str] = []
    for pattern, expansion in SYNONYMS:
        if re.search(pattern, q_low):
            extra.append(expansion)
    if not extra:
        return [q]
    return [q, f"{q} {' '.join(extra)}"]
