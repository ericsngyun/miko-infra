"""
Pydantic schemas for DemandPlan.

The DemandPlan is the structured blueprint for a demand letter, produced
by the legal planner (Stage 2) from the EvidenceGraph. It defines the
sections, damages breakdown, and citation requirements that the draft
renderer (Stage 3) uses to produce prose.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DamageItem(BaseModel):
    """A single line item in the damages breakdown."""

    model_config = ConfigDict(populate_by_name=True)

    category: str
    description: str
    amount: float
    source_fact_ids: list[str] = Field(default_factory=list, alias="sourceFactIds")
    notes: str | None = None

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class CitationRequirement(BaseModel):
    """A citation that must appear in a specific section of the demand letter."""

    model_config = ConfigDict(populate_by_name=True)

    section_name: str = Field(alias="sectionName")
    fact_id: str = Field(alias="factId")
    citation_type: str = Field(alias="citationType")
    required: bool = True

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class DemandSection(BaseModel):
    """A section of the demand letter plan."""

    model_config = ConfigDict(populate_by_name=True)

    section_name: str = Field(alias="sectionName")
    section_type: str = Field(alias="sectionType")
    order: int
    required_elements: list[str] = Field(default_factory=list, alias="requiredElements")
    source_fact_ids: list[str] = Field(default_factory=list, alias="sourceFactIds")
    instructions: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class DamagesBreakdown(BaseModel):
    """Complete damages breakdown for the demand letter."""

    model_config = ConfigDict(populate_by_name=True)

    special_damages: list[DamageItem] = Field(default_factory=list, alias="specialDamages")
    general_damages: list[DamageItem] = Field(default_factory=list, alias="generalDamages")
    total_special: float = Field(alias="totalSpecial")
    total_general: float = Field(alias="totalGeneral")
    demand_amount: float = Field(alias="demandAmount")
    multiplier: float | None = None

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class WeaknessFlag(BaseModel):
    """A weakness or risk flag identified during planning."""

    model_config = ConfigDict(populate_by_name=True)

    topic: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    description: str
    keywords: list[str] = Field(default_factory=list)
    mitigation: str | None = None

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")


class DemandPlan(BaseModel):
    """
    Complete demand letter plan.

    Produced by the legal planner from an EvidenceGraph. Consumed by
    the draft renderer to produce the final demand letter prose.
    """

    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    sections: list[DemandSection] = Field(default_factory=list)
    damages: DamagesBreakdown | None = None
    citation_requirements: list[CitationRequirement] = Field(
        default_factory=list, alias="citationRequirements"
    )
    weakness_flags: list[WeaknessFlag] = Field(
        default_factory=list, alias="weaknessFlags"
    )
    tone: str = "professional, assertive, empathetic"
    required_sections: list[str] = Field(
        default_factory=list, alias="requiredSections"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        raise NotImplementedError("TODO: implement in Stage 1")
