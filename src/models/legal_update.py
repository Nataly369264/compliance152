"""Models for legal updates / law changes tracking."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LegalUpdate(BaseModel):
    """A single legal change that affects document generation."""

    id: str
    date: str
    effective_date: str
    source: str
    source_url: str = ""
    title: str
    summary: str
    articles: list[str] = Field(default_factory=list)
    affected_documents: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    severity: str = "medium"  # critical, high, medium, low
    category: str = ""  # technical, consent, documentation, fines, etc.
