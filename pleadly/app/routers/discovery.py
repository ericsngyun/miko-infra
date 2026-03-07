"""
Discovery generation and review endpoints.

POST /discovery — generate discovery requests.
POST /discovery/review — review discovery responses.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import (
    DiscoveryPayload,
    DiscoveryResult,
    DiscoveryReviewPayload,
    DiscoveryReviewResult,
)

router = APIRouter()


@router.post("/discovery", response_model=DiscoveryResult)
async def generate_discovery(payload: DiscoveryPayload) -> DiscoveryResult:
    """
    Generate discovery requests (interrogatories, RFPs, RFAs).

    Creates jurisdiction-appropriate discovery requests based on case
    type, target party, and specified topics.

    Args:
        payload: DiscoveryPayload with case summary and discovery parameters.

    Returns:
        DiscoveryResult with generated discovery items.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")


@router.post("/discovery/review", response_model=DiscoveryReviewResult)
async def review_discovery(payload: DiscoveryReviewPayload) -> DiscoveryReviewResult:
    """
    Review opposing party's discovery responses for adequacy.

    Analyzes responses against original requests and known facts to
    identify evasive answers, missing information, and contradictions.

    Args:
        payload: DiscoveryReviewPayload with original requests and responses.

    Returns:
        DiscoveryReviewResult with review findings.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
