"""
Cross-document analysis endpoint.

POST /cross-analyze — analyze relationships across multiple documents.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import CrossAnalyzePayload, CrossAnalyzeResult

router = APIRouter()


@router.post("/cross-analyze", response_model=CrossAnalyzeResult)
async def cross_analyze(payload: CrossAnalyzePayload) -> CrossAnalyzeResult:
    """
    Perform cross-document analysis to find conflicts, gaps, and correlations.

    Analyzes multiple document analysis results together to identify
    inconsistencies, treatment gaps, and corroborating evidence.

    Args:
        payload: CrossAnalyzePayload with document summaries and firm context.

    Returns:
        CrossAnalyzeResult with findings, timing, and token usage.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
