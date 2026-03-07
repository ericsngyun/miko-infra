"""
Spend endpoint — returns daily cloud API spend for this project.
Stage 1 stub: returns 0.0 until cloud spend tracking is implemented.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SpendResult(BaseModel):
    usd: float
    period: str = "today"


@router.get("/spend", response_model=SpendResult)
async def get_spend() -> SpendResult:
    """Return today's cloud API spend. Stub returns 0.0 until Stage 1."""
    return SpendResult(usd=0.0)
