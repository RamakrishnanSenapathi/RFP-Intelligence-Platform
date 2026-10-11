"""Part A – parsing HTML and PDF bid documents into clean, page-structured text with tables."""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import ParsedDocument, ParsedPage

log = logging.getLogger("rfp.ingestion")

SUPPORTED_EXT = {".pdf", ".html", ".htm", ".txt", ".md"}

# --------------------------------------------------------------------------- metadata
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
           "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec")
_DATE_PATTERNS = [
    (re.compile(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b"), "mdy_text"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "mdy_num"),
]
_ADDENDUM_NUM = re.compile(r"addend(?:um|a)[\s_\-]*(?:no\.?|number|#)?[\s_\-:.]*(\d+)", re.I)


def find_date(text: str) -> Optional[str]:
    """Earliest date-looking string in the first 2000 chars, as ISO (YYYY-MM-DD)."""
    sample = text[:2000]
    best: Optional[tuple[int, str]] = None
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(sample)
        if not m:
            continue
        try:
            if kind == "mdy_text":
                d = datetime(int(m.group(3)), _MONTH_NUM[m.group(1)[:3].lower()], int(m.group(2)))
            elif kind == "iso":
                d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                d = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except (ValueError, KeyError):
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), d.date().isoformat())
    return best[1] if best else None


def detect_doc_type(file_name: str, text_sample: str = "") -> tuple[str, Optional[int]]:
    """Classify a file as bid_page / rfp / addendum / specs / affidavit (+ addendum number).

    File-name rules first, then content rules so unseen folders with odd names still work.
    """
    name = file_name.lower()
    ext = Path(name).suffix
    m = _ADDENDUM_NUM.search(name)
    if m:
        return "addendum", int(m.group(1))
    if "addendum" in name or "addenda" in name:
        m2 = _ADDENDUM_NUM.search(text_sample[:600])
        return "addendum", int(m2.group(1)) if m2 else None
    if ext in (".html", ".htm"):
        return "bid_page", None
    if "affidavit" in name:
        return "affidavit", None
    if re.search(r"\bspecs?\b|specification", name):
        return "specs", None
    head = text_sample[:500].lower()
    if re.search(r"\baddendum\b", head):
        m3 = _ADDENDUM_NUM.search(text_sample[:600])
        return "addendum", int(m3.group(1)) if m3 else None
    if "affidavit" in head[:250]:
        return "affidavit", None
    return "rfp", None


# --------------------------------------------------------------------------- cleaning
def _norm_line(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def remove_repeated_lines(pages: list[str], min_pages: int = 3, ratio: float = 0.6) -> list[str]:
    """Drop running headers/footers/page numbers: short edge lines that repeat on most pages."""
    if len(pages) < min_pages:
        return pages
    counts: Counter = Counter()
    for p in pages:
        lines = [l for l in p.splitlines() if l.strip()]
        edge = lines[:3] + lines[-3:]
        for n in {_norm_line(l) for l in edge if len(l.strip()) < 120}:
            counts[n] += 1
    threshold = max(2, int(len(pages) * ratio))
    bad = {n for n, c in counts.items() if c >= threshold}
    out = []
    for p in pages:
        lines = p.splitlines()
        idx = [i for i, l in enumerate(lines) if l.strip()]
        edge_idx = set(idx[:3] + idx[-3:])
        out.append("\n".join(l for i, l in enumerate(lines) if not (i in edge_idx and _norm_line(l) in bad)))
    return out


def _should_join(prev: str, cur: str) -> bool:
    p = prev.strip()
    if not p or prev.lstrip().startswith("|") or cur.lstrip().startswith(("|", "-", "•", "*", "#")):
        return False
    if re.search(r"[.:;!?)\]]$", p) or len(p) < 20:
        return False
    return cur.lstrip()[:1].islower()


def normalize_text(text: str) -> str:
    """Fix hyphenation + broken lines, normalise whitespace (tables and headings are preserved)."""
    text = (text.replace("\u00a0", " ").replace("\u00ad", "").replace("\ufb01", "fi").replace("\ufb02", "fl")
            .replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)
    out: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip()
        if out and line and _should_join(out[-1], line):
            out[-1] = out[-1] + " " + line.lstrip()
        else:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def rows_to_markdown(rows: list[list]) -> str:
    clean = []
    for r in rows or []:
        cells = [re.sub(r"\s+", " ", (c or "")).strip().replace("|", "/") for c in r]
        if any(cells):
            clean.append(cells)
    if not clean or (len(clean) == 1 and len(clean[0]) <= 1):
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    lines = ["| " + " | ".join(clean[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- PDF
def _overlap_ratio(rect, other) -> float:
    inter = rect & other
    if inter.is_empty:
        return 0.0
    area = max(rect.width * rect.height, 1e-6)
    return (inter.width * inter.height) / area


def _extract_pdf_page(page) -> tuple[str, bool]:
    import fitz  # PyMuPDF

    items: list[tuple[float, float, str]] = []
    table_rects = []
    has_tables = False
    try:
        for t in page.find_tables().tables:
            md = rows_to_markdown(t.extract())
            if md:
                items.append((t.bbox[1], t.bbox[0], md))
                table_rects.append(fitz.Rect(t.bbox))
                has_tables = True
    except Exception as e:  # find_tables is best-effort
        log.debug("find_tables failed: %s", e)
    for b in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, txt, _no, btype = b[:7]
        if btype != 0 or not txt.strip():
            continue
        r = fitz.Rect(x0, y0, x1, y1)
        if any(_overlap_ratio(r, tr) > 0.5 for tr in table_rects):
            continue
        items.append((y0, x0, txt.strip()))
    items.sort(key=lambda t: (round(t[0], 1), t[1]))
    return "\n\n".join(t[2] for t in items), has_tables


def parse_pdf(path: Path, bid_id: str, file_name: Optional[str] = None) -> ParsedDocument:
    import fitz

    file_name = file_name or path.name
    warnings: list[str] = []
    raw: list[tuple[str, bool]] = []
    meta_date = None
    with fitz.open(path) as doc:
        m = (doc.metadata or {}).get("creationDate", "") or ""
        mm = re.match(r"D:(\d{4})(\d{2})(\d{2})", m)
        if mm:
            meta_date = f"{mm.group(1)}-{mm.group(2)}-{mm.group(3)}"
        for i, page in enumerate(doc, start=1):
            try:
                text, has_tables = _extract_pdf_page(page)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"page {i}: extraction failed ({e})")
                text, has_tables = "", False
            if len(text.strip()) < 20:
                ocr_text = ""
                try:  # OCR fallback (needs Tesseract installed); bonus feature
                    tp = page.get_textpage_ocr(flags=0, full=True)
                    ocr_text = page.get_text("text", textpage=tp)
                except Exception:
                    pass
                if len(ocr_text.strip()) >= 20:
                    text = ocr_text
                    warnings.append(f"page {i}: scanned/empty page recovered with OCR")
                else:
                    warnings.append(f"page {i}: empty or scanned page, no text extracted (install Tesseract for OCR)")
            raw.append((text, has_tables))
    texts = remove_repeated_lines([t for t, _ in raw])
    pages = [ParsedPage(i, normalize_text(t), raw[i - 1][1]) for i, t in enumerate(texts, start=1)]
    sample = next((p.text for p in pages if p.text), "")
    doc_type, add_no = detect_doc_type(file_name, sample)
    return ParsedDocument(bid_id, file_name, str(path), doc_type, add_no,
                          find_date(sample) or meta_date, pages, warnings)


# --------------------------------------------------------------------------- HTML
_BLOCK_TAGS = ["p", "div", "li", "tr", "section", "article", "dt", "dd", "ul", "ol", "table", "br",
               "header", "footer", "main", "form", "blockquote", "pre"]


def parse_html(path: Path, bid_id: str, file_name: Optional[str] = None) -> ParsedDocument:
    from bs4 import BeautifulSoup

    file_name = file_name or path.name
    raw = path.read_bytes().decode("utf-8", errors="replace")
    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        soup = BeautifulSoup(raw, "html.parser")
    warnings: list[str] = []
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for t in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "template"]):
        t.decompose()
    for table in reversed(soup.find_all("table")):  # innermost first
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
        md = rows_to_markdown(rows)
        table.replace_with(soup.new_string(f"\n\n{md}\n\n" if md else "\n"))
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        txt = h.get_text(" ", strip=True)
        h.replace_with(soup.new_string(f"\n\n## {txt}\n\n" if txt else "\n"))
    for dt in soup.find_all("dt"):
        dt.append(": ")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text("")
    text = normalize_text(re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text))
    if title and title.lower() not in text[:400].lower():
        text = f"## {title}\n\n{text}"
    if len(text) < 50:
        warnings.append("HTML page produced almost no text")
    doc_type, add_no = detect_doc_type(file_name, text)
    pages = [ParsedPage(1, text, "|" in text)]
    return ParsedDocument(bid_id, file_name, str(path), doc_type, add_no, find_date(text), pages, warnings)


def parse_text(path: Path, bid_id: str, file_name: Optional[str] = None) -> ParsedDocument:
    file_name = file_name or path.name
    text = normalize_text(path.read_text(encoding="utf-8", errors="replace"))
    doc_type, add_no = detect_doc_type(file_name, text)
    return ParsedDocument(bid_id, file_name, str(path), doc_type, add_no, find_date(text),
                          [ParsedPage(1, text, False)], [])


def parse_file(path: Path, bid_id: str, file_name: Optional[str] = None) -> ParsedDocument:
    """Dispatch by extension. Never raises: failures become warnings on an empty document."""
    path = Path(path)
    file_name = file_name or path.name
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return parse_pdf(path, bid_id, file_name)
        if ext in (".html", ".htm"):
            return parse_html(path, bid_id, file_name)
        return parse_text(path, bid_id, file_name)
    except Exception as e:  # noqa: BLE001
        log.exception("failed to parse %s", path)
        dt, an = detect_doc_type(file_name)
        return ParsedDocument(bid_id, file_name, str(path), dt, an, None, [], [f"unreadable file: {e!r}"])
