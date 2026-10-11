"""Section- and table-aware chunking tuned for RFPs.

* Chunks never cross page boundaries (exact page citations).
* Headings start new sections; small sections are merged to avoid tiny chunks.
* Markdown tables become their own chunks (row-split with the header repeated if too long).
* Long paragraphs are split on sentence boundaries with character overlap;
  size-triggered flushes carry the last short paragraph into the next chunk.
"""
from __future__ import annotations

import re

from .models import Chunk, ParsedDocument


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 110 or s.startswith("|"):
        return False
    if s.startswith("#"):
        return True
    if re.match(r"^(?i:section|article|part|attachment|exhibit|appendix|addendum)\s+(\d+[\w.\-]*|[IVXLC]+|[A-Z])\b.{0,70}$", s):
        return True
    if re.match(r"^\d{1,2}(\.\d{1,2}){0,3}[.)]?\s+[A-Z][^.!?]{2,90}$", s):
        return True
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.8 and not s.endswith((".", ","))


def _is_table_block(lines: list[str]) -> bool:
    return len(lines) >= 2 and all(l.lstrip().startswith("|") for l in lines if l.strip())


def _split_blocks(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def _split_long(par: str, max_chars: int, overlap: int) -> list[str]:
    parts: list[str] = []
    cur = ""
    for s in re.split(r"(?<=[.!?;])\s+", par):
        if len(s) > max_chars:
            if cur:
                parts.append(cur)
                cur = ""
            step = max(max_chars - overlap, 1)
            parts.extend(s[i : i + max_chars] for i in range(0, len(s), step))
            continue
        if cur and len(cur) + len(s) + 1 > max_chars:
            parts.append(cur)
            tail = cur[-overlap:] if overlap else ""
            tail = tail[tail.find(" ") + 1 :] if " " in tail else tail
            cur = (tail + " " + s).strip()
        else:
            cur = (cur + " " + s).strip()
    if cur:
        parts.append(cur)
    return parts


def _split_table(md: str, max_chars: int) -> list[str]:
    if len(md) <= max_chars:
        return [md]
    lines = md.splitlines()
    header = lines[:2] if len(lines) > 2 and set(lines[1].replace("|", "").replace(" ", "")) <= {"-"} else []
    rows = lines[len(header):]
    out, cur = [], []
    size = sum(len(h) + 1 for h in header)
    for r in rows:
        if cur and size + len(r) + 1 > max_chars:
            out.append("\n".join(header + cur))
            cur, size = [], sum(len(h) + 1 for h in header)
        cur.append(r)
        size += len(r) + 1
    if cur:
        out.append("\n".join(header + cur))
    return out


class _Builder:
    def __init__(self, max_chars: int, overlap: int, min_chars: int):
        self.max, self.overlap, self.min = max_chars, overlap, min_chars
        self.pieces: list[tuple[str, str, bool]] = []
        self.buf: list[str] = []
        self.buf_len = 0
        self.buf_section = ""
        self.fresh = False  # buffer holds only an overlap carry-over

    def _flush(self, carry: bool) -> None:
        if not self.buf:
            return
        if not self.fresh:
            text = "\n\n".join(self.buf).strip()
            if text:
                self.pieces.append((text, self.buf_section, False))
        last = self.buf[-1]
        self.buf, self.buf_len, self.fresh = [], 0, False
        if carry and len(last) <= self.overlap * 2:
            self.buf, self.buf_len, self.fresh = [last], len(last) + 2, True

    def _add(self, text: str, section: str) -> None:
        if self.buf and self.buf_len + len(text) + 2 > self.max:
            if self.fresh:
                self.buf, self.buf_len, self.fresh = [], 0, False
            else:
                self._flush(carry=True)
        if not self.buf or self.fresh:
            self.buf_section = section
        self.buf.append(text)
        self.buf_len += len(text) + 2
        self.fresh = False

    def add_text(self, par: str, section: str) -> None:
        if len(par) > self.max:
            for part in _split_long(par, self.max, self.overlap):
                self._add(part, section)
        else:
            self._add(par, section)

    def new_section(self, heading: str) -> None:
        if self.buf_len >= self.min and not self.fresh:
            self._flush(carry=False)
        self.fresh = False
        self._add(heading, heading)

    def add_table(self, md: str, section: str) -> None:
        self._flush(carry=False)
        self.buf, self.buf_len, self.fresh = [], 0, False
        for part in _split_table(md, self.max):
            self.pieces.append((part, section, True))

    def finish(self) -> list[tuple[str, str, bool]]:
        if not self.fresh:
            self._flush(carry=False)
        return self.pieces


def _merge_tiny(pieces: list[tuple[str, str, bool]], min_chars: int) -> list[tuple[str, str, bool]]:
    merged: list[tuple[str, str, bool]] = []
    for text, sec, tbl in pieces:
        if merged and not merged[-1][2] and not tbl and len(merged[-1][0]) < min_chars:
            pt, ps, _ = merged[-1]
            merged[-1] = (pt + "\n\n" + text, ps, False)
        else:
            merged.append((text, sec, tbl))
    return merged


def chunk_document(doc: ParsedDocument, max_chars: int = 1200, overlap: int = 150,
                   min_chars: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    section = ""
    for page in doc.pages:
        if not page.text.strip():
            continue
        b = _Builder(max_chars, overlap, min_chars)
        for block in _split_blocks(page.text):
            lines = block.split("\n")
            if _is_table_block(lines):
                b.add_table(block, section)
                continue
            if is_heading(lines[0]):
                section = lines[0].strip().lstrip("# ").strip()
                b.new_section(section)
                rest = "\n".join(lines[1:]).strip()
                if rest:
                    b.add_text(rest, section)
                continue
            b.add_text(block, section)
        for i, (text, sec, tbl) in enumerate(_merge_tiny(b.finish(), min_chars)):
            chunks.append(Chunk(
                chunk_id=f"{doc.bid_id}::{doc.file_name}::p{page.page_number}::c{i}",
                bid_id=doc.bid_id, file_name=doc.file_name, doc_type=doc.doc_type,
                addendum_number=doc.addendum_number, page_number=page.page_number,
                section=sec, text=text, doc_date=doc.doc_date, is_table=tbl))
    return chunks
