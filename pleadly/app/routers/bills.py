"""
Bill audit endpoint.

POST /bills/audit — medical bill audit and analysis.

STUB — to be implemented in Stage 1.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.payloads import BillAuditPayload, BillAuditResult

router = APIRouter()


@router.post("/bills/audit", response_model=BillAuditResult)
async def audit_bill(payload: BillAuditPayload) -> BillAuditResult:
    """
    Audit a medical bill for reasonableness and accuracy.

    Checks for duplicate charges, unreasonable amounts, coding errors,
    and treatments unrelated to the injury date.

    Args:
        payload: BillAuditPayload with bill data and case context.

    Returns:
        BillAuditResult with audit findings, flags, and adjusted amounts.

    Raises:
        NotImplementedError: This is a stub.
    """
    raise NotImplementedError("TODO: implement in Stage 1")
