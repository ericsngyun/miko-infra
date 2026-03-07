"""
Pydantic schemas for EvidenceGraph nodes and edges.

The EvidenceGraph is the structured representation of all extracted facts,
documents, providers, and timeline events for a case. It serves as the
input to the legal planner (Stage 2).

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FactNode(BaseModel):
    """A single extracted fact from a source document."""

    model_config = ConfigDict(populate_by_name=True)

    fact_id: str = Field(alias="factId")
    text: str
    category: str
    confidence: float
    source_document_id: str = Field(alias="sourceDocumentId")
    page_number: int | None = Field(default=None, alias="pageNumber")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class DocumentNode(BaseModel):
    """A source document in the evidence graph."""

    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(alias="documentId")
    document_type: str = Field(alias="documentType")
    title: str
    date: str | None = None
    provider_id: str | None = Field(default=None, alias="providerId")
    page_count: int | None = Field(default=None, alias="pageCount")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class ProviderNode(BaseModel):
    """A medical provider, insurer, or other entity in the evidence graph."""

    model_config = ConfigDict(populate_by_name=True)

    provider_id: str = Field(alias="providerId")
    name: str
    provider_type: str = Field(alias="providerType")
    specialty: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class TimelineNode(BaseModel):
    """A dated event in the case timeline."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    date: str
    event_type: str = Field(alias="eventType")
    description: str
    source_fact_ids: list[str] = Field(default_factory=list, alias="sourceFactIds")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class GraphEdge(BaseModel):
    """An edge connecting two nodes in the evidence graph."""

    model_config = ConfigDict(populate_by_name=True)

    source_id: str = Field(alias="sourceId")
    target_id: str = Field(alias="targetId")
    edge_type: str = Field(alias="edgeType")
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class EvidenceGraph(BaseModel):
    """
    Complete evidence graph for a case.

    Contains all extracted facts, documents, providers, timeline events,
    and the edges connecting them.
    """

    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    facts: list[FactNode] = Field(default_factory=list)
    documents: list[DocumentNode] = Field(default_factory=list)
    providers: list[ProviderNode] = Field(default_factory=list)
    timeline: list[TimelineNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")
