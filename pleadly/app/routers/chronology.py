"""
Chronology generation endpoint.

POST /chronology — medical record to chronology extraction.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import ChronologyPayload, ChronologyResult

router = APIRouter()


@router.post("/chronology", response_model=ChronologyResult)
async def generate_chronology(payload: ChronologyPayload) -> ChronologyResult:
    """
    Generate a medical chronology from document texts.

    Extracts dates, providers, treatments, and identifies gaps in
    the treatment timeline.

    Args:
        payload: ChronologyPayload with document texts and firm context.

    Returns:
        ChronologyResult with timeline entries and gaps.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
