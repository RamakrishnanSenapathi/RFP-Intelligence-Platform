"""Pydantic models for all LLM / agent-to-agent structured messages."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Citation(BaseModel):
    evidence_id: str = Field(description="Evidence label such as E3")
    quote: str = Field(default="", description="Short verbatim quote (<=200 chars) copied from that evidence")


class FieldExtraction(BaseModel):
    field: str
    value: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""


class GroupExtraction(BaseModel):
    entries: list[FieldExtraction]


class AddendumChange(BaseModel):
    field: Optional[str] = Field(default=None, description="Exact output field name affected, or null")
    change_summary: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    evidence_quote: str = ""
    page: Optional[int] = None


class AddendumAnalysis(BaseModel):
    changes: list[AddendumChange] = Field(default_factory=list)


class FieldVerdict(BaseModel):
    field: str
    verdict: Literal["supported", "partial", "unsupported"]
    confidence: float = 0.0
    issue: str = ""


class ValidationBatch(BaseModel):
    verdicts: list[FieldVerdict]


class QueryRewrite(BaseModel):
    queries: list[str] = Field(default_factory=list)


class SubQuery(BaseModel):
    query: str
    bid_id: Optional[str] = None
    doc_type: Optional[str] = None
    addendum_number: Optional[int] = None


class QueryPlan(BaseModel):
    sub_queries: list[SubQuery]


class Answer(BaseModel):
    answer: str
    used_evidence: list[str] = Field(default_factory=list)
    found: bool = True
    confidence: float = 0.0


class Summary(BaseModel):
    summary: str
