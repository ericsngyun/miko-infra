"""
Pydantic schema for Citation Binder output.

The Citation Binder maps each citation in a demand letter draft back to
the specific source document, page, and extracted fact that supports it.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CitationEntry(BaseModel):
    """A single citation mapping in the binder."""

    model_config = ConfigDict(populate_by_name=True)

    citation_id: str = Field(alias="citationId")
    fact_id: str = Field(alias="factId")
    source_document: str = Field(alias="sourceDocument")
    page: int | None = None
    confidence: float
    source_text: str | None = Field(default=None, alias="sourceText")
    draft_location: str | None = Field(default=None, alias="draftLocation")

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class CitationBinder(BaseModel):
    """
    Complete citation binder for a demand letter draft.

    Maps every factual assertion in the draft to its source evidence.
    """

    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    demand_id: str = Field(alias="demandId")
    citations: list[CitationEntry] = Field(default_factory=list)
    coverage_score: float = Field(alias="coverageScore")
    unmatched_assertions: list[str] = Field(
        default_factory=list, alias="unmatchedAssertions"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")
