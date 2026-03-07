"""
Demand rating submission endpoint.

POST /demand/rating — submit attorney feedback on a demand letter.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import DemandRatingPayload

router = APIRouter()


@router.post("/demand/rating")
async def submit_demand_rating(payload: DemandRatingPayload) -> dict[str, bool]:
    """
    Submit an attorney's rating/feedback on a generated demand letter.

    Used for RLHF-style feedback to improve future demand letter quality.
    Stores the rating and optionally triggers retraining signals.

    Args:
        payload: DemandRatingPayload with demand ID, rating, and notes.

    Returns:
        Dict with ok=True on success.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
