"""
Lien reduction endpoint.

POST /liens/reduce — lien reduction analysis and negotiation strategy.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import LienReducePayload, LienReduceResult

router = APIRouter()


@router.post("/liens/reduce", response_model=LienReduceResult)
async def reduce_lien(payload: LienReducePayload) -> LienReduceResult:
    """
    Analyze a lien and generate reduction arguments.

    Evaluates the lien against settlement amount, attorney fees,
    costs, and comparative fault to produce reduction strategies
    with recommended negotiated amounts.

    Args:
        payload: LienReducePayload with lien data and settlement context.

    Returns:
        LienReduceResult with reduction analysis and recommendations.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
