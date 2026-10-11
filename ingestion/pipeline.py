"""Ingestion pipeline: folder -> parsed documents -> chunks (+ processed JSON dump and a log)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from config import Settings, get_settings
from .chunker import chunk_document
from .models import Chunk, ParsedDocument
from .parser import SUPPORTED_EXT, parse_file

log = logging.getLogger("rfp.ingestion")


@dataclass
class IngestResult:
    bid_id: str
    documents: list[ParsedDocument] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        return [f"{e['file']}: {e['message']}" for e in self.events if e["level"] in ("warning", "error")]


def list_bid_files(bid_dir: Path) -> list[Path]:
    return sorted(p for p in Path(bid_dir).rglob("*") if p.is_file() and not p.name.startswith("."))


def folder_fingerprint(bid_dir: Path) -> str:
    """Content hash of the folder; unchanged folders are skipped by incremental indexing."""
    h = hashlib.sha1()
    root = Path(bid_dir)
    for p in list_bid_files(root):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(hashlib.sha1(p.read_bytes()).digest())
    return h.hexdigest()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]


def ingest_folder(bid_dir: Path, settings: Optional[Settings] = None, bid_id: Optional[str] = None,
                  dump: bool = True) -> IngestResult:
    settings = settings or get_settings()
    bid_dir = Path(bid_dir)
    bid_id = bid_id or bid_dir.name
    result = IngestResult(bid_id=bid_id)

    def ev(file: str, level: str, message: str) -> None:
        result.events.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "bid_id": bid_id,
                              "file": file, "level": level, "message": message})
        getattr(log, "warning" if level != "info" else "info")("[%s] %s: %s", bid_id, file, message)

    if not bid_dir.is_dir():
        ev(str(bid_dir), "error", "bid folder does not exist")
        return result

    for path in list_bid_files(bid_dir):
        rel = path.relative_to(bid_dir).as_posix()
        if path.suffix.lower() not in SUPPORTED_EXT:
            ev(rel, "warning", f"unsupported file type {path.suffix!r}, skipped")
            continue
        doc = parse_file(path, bid_id, file_name=rel)
        for w in doc.warnings:
            ev(rel, "warning", w)
        if doc.n_chars == 0:
            ev(rel, "error", "no extractable text; document skipped")
            continue
        chunks = chunk_document(doc, settings.chunk_max_chars, settings.chunk_overlap_chars, settings.chunk_min_chars)
        ev(rel, "info", f"parsed as {doc.doc_type}"
                        + (f" #{doc.addendum_number}" if doc.addendum_number else "")
                        + f": {len(doc.pages)} page(s), {len(chunks)} chunk(s)")
        result.documents.append(doc)
        result.chunks.extend(chunks)

    if dump:
        try:
            out = settings.processed_dir / bid_id
            out.mkdir(parents=True, exist_ok=True)
            for d in result.documents:
                (out / f"{_safe(d.file_name)}.json").write_text(
                    json.dumps(asdict(d), ensure_ascii=False, indent=1), encoding="utf-8")
            settings.log_dir.mkdir(parents=True, exist_ok=True)
            with open(settings.log_dir / "ingestion.jsonl", "a", encoding="utf-8") as f:
                for e in result.events:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("could not write processed dump: %s", e)
    return result
