from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class ParsedPage:
    page_number: int
    text: str
    has_tables: bool = False


@dataclass
class ParsedDocument:
    bid_id: str
    file_name: str
    path: str
    doc_type: str
    addendum_number: Optional[int]
    doc_date: Optional[str]
    pages: list[ParsedPage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def n_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


@dataclass
class Chunk:
    chunk_id: str
    bid_id: str
    file_name: str
    doc_type: str
    addendum_number: Optional[int]
    page_number: int
    section: str
    text: str
    doc_date: Optional[str] = None
    is_table: bool = False

    def embed_text(self) -> str:
        """Text sent to the embedder: metadata header + content (gives the vector document context)."""
        tag = self.doc_type + (f" {self.addendum_number}" if self.addendum_number else "")
        head = f"[{self.bid_id} | {tag} | {self.file_name} | page {self.page_number}]"
        if self.section:
            head += f" {self.section}"
        return f"{head}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)
