"""
Demand section editing endpoint.

POST /demand/edit-section — AI-assisted editing of a demand letter section.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import DemandEditPayload, DemandEditResult

router = APIRouter()


@router.post("/demand/edit-section", response_model=DemandEditResult)
async def edit_demand_section(payload: DemandEditPayload) -> DemandEditResult:
    """
    Edit a specific section of a demand letter based on attorney instructions.

    Takes the current section content and attorney edit instructions,
    and produces a revised section with tracked changes.

    Args:
        payload: DemandEditPayload with section name, content, and instructions.

    Returns:
        DemandEditResult with edited section, changes made, and notes.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
