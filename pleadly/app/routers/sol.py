"""
SOL scan endpoint — statute of limitations deadline calculation.

POST /sol-scan — calls the fully-implemented sol_engine.

STUB router, but delegates to the fully-implemented sol_engine.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from models.payloads import SOLPayload, SOLResult
from pipeline.sol_engine import calculate_sol

logger = logging.getLogger("pleadly.sol")

router = APIRouter()


@router.post("/sol-scan", response_model=SOLResult)
async def sol_scan(payload: SOLPayload) -> SOLResult:
    """
    Calculate statute of limitations deadline.

    Delegates to the deterministic SOL engine. No LLM calls required.
    """
    # request_id is logged by middleware — never log payload content
    logger.info("SOL scan requested for jurisdiction=%s", payload.jurisdiction)

    calculation = calculate_sol(
        jurisdiction=payload.jurisdiction,
        case_type=payload.case_type,
        incident_date=payload.incident_date,
        client_dob=payload.client_dob,
        defendant_type=payload.defendant_type,
        is_minor=payload.is_minor,
        government_entity=payload.government_entity,
        discovery_date=payload.discovery_date,
        additional_facts=payload.additional_facts,
    )

    return SOLResult(
        deadline=calculation.deadline.isoformat(),
        statute_cited=calculation.statute_cited,
        sol_period=calculation.sol_period,
        tolling_applicable=calculation.tolling_applied if calculation.tolling_applied else None,
        government_tort_notice_deadline=(
            calculation.government_tort_notice_deadline.isoformat()
            if calculation.government_tort_notice_deadline
            else None
        ),
        special_considerations=calculation.special_considerations,
        verify_items=calculation.verify_items,
        recommendation=calculation.recommendation,
        alert_dates=[d.isoformat() for d in calculation.alert_dates],
        result={
            "deadline": calculation.deadline.isoformat(),
            "statute_cited": calculation.statute_cited,
            "sol_period": calculation.sol_period,
            "tolling_applied": calculation.tolling_applied,
            "alert_dates": [d.isoformat() for d in calculation.alert_dates],
        },
    )
