"""Versioned contracts for model drafts and server-validated answers."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import StrictModel


class EvidenceSpan(StrictModel):
    id: str = Field(pattern=r"^s(?:[1-9]|[1-7][0-9]|80)$")
    kind: Literal["fact", "public_fact", "inference", "scenario", "knowledge"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    refs: list[str] = Field(default_factory=list, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def ordered_offsets(self):
        if self.end <= self.start:
            raise ValueError("Evidence span end must be greater than start")
        return self


class ViewRequest(StrictModel):
    id: str = Field(pattern=r"^v(?:[1-8])$")
    kind: Literal["table", "bar", "line"]
    result_ref: UUID
    fields: list[str] = Field(default_factory=list, max_length=16)
    title: str = Field(max_length=120)
    sort_by: str | None = Field(default=None, max_length=80)
    descending: bool = False


class AnswerDraft21(StrictModel):
    schema_version: Literal["2.1"]
    body_markdown: str = Field(max_length=24000)
    spans: list[EvidenceSpan] = Field(default_factory=list, max_length=80)
    views: list[ViewRequest] = Field(default_factory=list, max_length=8)


class AnswerBlock(StrictModel):
    id: str = Field(pattern=r"^s(?:[1-9]|[1-7][0-9]|80)$")
    kind: Literal["fact", "public_fact", "inference", "scenario", "knowledge"]
    text: str = Field(min_length=1, max_length=24000)
    refs: list[str] = Field(default_factory=list, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=80)


class ModelAnswer21(StrictModel):
    """Model-facing blocks; the persisted contract retains server-owned offsets."""
    schema_version: Literal["2.1"]
    blocks: list[AnswerBlock] = Field(default_factory=list, max_length=80)
    views: list[ViewRequest] = Field(default_factory=list, max_length=8)

    def compile(self) -> AnswerDraft21:
        parts, spans = [], []
        offset = 0
        for block in self.blocks:
            spans.append(EvidenceSpan(id=block.id, kind=block.kind, start=offset,
                end=offset + len(block.text), refs=block.refs, depends_on=block.depends_on))
            parts.append(block.text)
            offset += len(block.text) + 2
        return AnswerDraft21(schema_version="2.1", body_markdown="\n\n".join(parts), spans=spans, views=self.views)


class EvidenceItem(StrictModel):
    id: str
    kind: Literal["internal", "public"]
    result_ref: str
    source_ref: str | None = None
    title: str
    url: str | None = None
    excerpt: str | None = None
    fetch_status: Literal["internal", "full_text", "snippet_only", "truncated"]
    published_at: str | None = None
    captured_at: datetime
    unit: str | None = None


class Limitation(StrictModel):
    code: str
    message: str
    affected_span_ids: list[str] = Field(default_factory=list)
    affected_view_ids: list[str] = Field(default_factory=list)


class ValidatedAnswer21(StrictModel):
    schema_version: Literal["2.1"] = "2.1"
    delivery_status: Literal["complete", "partial", "failed"]
    body_markdown: str = Field(max_length=24000)
    plain_text: str = Field(max_length=24000)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=100)
    views: list[dict] = Field(default_factory=list, max_length=8)
    limitations: list[Limitation] = Field(default_factory=list, max_length=80)
