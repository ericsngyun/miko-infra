"""
Stage 2: Legal Planner — EvidenceGraph to DemandPlan JSON.

Analyzes the evidence graph to produce a structured demand plan that
defines sections, damages breakdown, citation requirements, and
weakness flags.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from typing import Any

from models.demand_plan import DemandPlan
from models.evidence_graph import EvidenceGraph


async def plan_demand(
    *,
    evidence_graph: EvidenceGraph,
    case_type: str,
    jurisdiction: str,
    firm_context: dict[str, Any],
    attorney_instructions: str | None = None,
) -> DemandPlan:
    """
    Analyze an EvidenceGraph and produce a DemandPlan.

    Args:
        evidence_graph: The structured evidence for the case.
        case_type: Type of case (auto_accident, slip_and_fall, etc.).
        jurisdiction: Two-letter state code.
        firm_context: Firm configuration dict.
        attorney_instructions: Optional attorney-provided instructions.

    Returns:
        A DemandPlan with sections, damages, citations, and weakness flags.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")


async def identify_weaknesses(
    *,
    evidence_graph: EvidenceGraph,
    case_type: str,
) -> list[dict[str, Any]]:
    """
    Identify potential weaknesses in the case evidence.

    Args:
        evidence_graph: The structured evidence for the case.
        case_type: Type of case.

    Returns:
        List of weakness flag dicts with topic, severity, description, keywords.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
