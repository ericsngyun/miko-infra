"""
Stage 3: Draft Renderer — DemandPlan to prose + Citation Binder.

Takes a DemandPlan and produces the actual demand letter prose along with
a Citation Binder that maps every factual assertion back to its source.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any

from models.citation_map import CitationBinder
from models.demand_plan import DemandPlan


async def render_draft(
    *,
    demand_plan: DemandPlan,
    firm_context: dict[str, Any],
    tone: str = "professional, assertive, empathetic",
) -> tuple[str, CitationBinder]:
    """
    Render a demand letter draft from a DemandPlan.

    Args:
        demand_plan: The structured demand plan.
        firm_context: Firm configuration for letterhead, attorney info, etc.
        tone: Target tone for the letter.

    Returns:
        A tuple of (draft_text, citation_binder).

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")


async def render_section(
    *,
    section_name: str,
    section_plan: dict[str, Any],
    evidence_facts: list[dict[str, Any]],
    tone: str = "professional, assertive, empathetic",
) -> tuple[str, list[dict[str, Any]]]:
    """
    Render a single section of the demand letter.

    Args:
        section_name: Name of the section to render.
        section_plan: The section plan from the DemandPlan.
        evidence_facts: Facts from the EvidenceGraph relevant to this section.
        tone: Target tone.

    Returns:
        A tuple of (section_text, citations_for_section).

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
