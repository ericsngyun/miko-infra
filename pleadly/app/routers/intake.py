"""
Intake scoring endpoint.

POST /intake — case value scoring based on intake data.

Uses qwen2.5:1.5b for fast viability scoring.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException

from integrations.ollama_client import MODEL_CLASSIFIER
from main import app_state
from models.payloads import IntakePayload, IntakeResult

logger = logging.getLogger("pleadly.intake")

router = APIRouter()


@router.post("/intake", response_model=IntakeResult)
async def score_intake(payload: IntakePayload) -> IntakeResult:
    """
    Score an intake lead for case viability.

    Evaluates accident type, injuries, fault, medical treatment status,
    and jurisdiction to produce a viability score and signal.

    Args:
        payload: IntakePayload with intake call data.

    Returns:
        IntakeResult with score, viability signal, and contributing factors.
    """
    logger.info("Scoring intake case for caller=%s", payload.caller_name or "unknown")

    # Build structured intake data
    intake_data = {
        "caller_name": payload.caller_name,
        "accident_type": payload.accident_type,
        "accident_date": payload.accident_date,
        "injuries_described": payload.injuries_described,
        "sought_medical_treatment": payload.sought_medical_treatment,
        "other_party_at_fault": payload.other_party_at_fault,
        "jurisdiction": payload.jurisdiction,
    }

    system_prompt = """You are a legal intake specialist evaluating personal injury case viability.

Score the case from 0-100 based on these factors:
- Accident type and severity
- Injuries described
- Medical treatment sought
- Clear fault by other party
- Jurisdiction (some jurisdictions favor plaintiffs)

Assign a viability signal:
- strong: Clear liability, significant injuries, medical treatment, high settlement potential
- moderate: Some liability questions OR moderate injuries, worth pursuing
- weak: Questionable liability OR minor injuries, low settlement potential
- unclear: Insufficient information to assess

Respond in JSON format:
{
  "score": 0-100,
  "viabilitySignal": "strong|moderate|weak|unclear",
  "factors": ["list of factors that influenced the score"]
}"""

    user_prompt = f"""/no_think

Intake data:
{json.dumps(intake_data, indent=2)}

Evaluate case viability."""

    try:
        ollama = app_state["ollama"]
        response = await ollama.chat_json(
            prompt=user_prompt,
            model=MODEL_CLASSIFIER,
            system=system_prompt,
            temperature=0.1,
            timeout=30.0,
        )

        # Extract results
        score = float(response.get("score", 0.0))
        viability_signal_raw = response.get("viabilitySignal", "unclear")

        # Validate viability signal
        valid_signals: list[Literal["strong", "moderate", "weak", "unclear"]] = [
            "strong", "moderate", "weak", "unclear"
        ]
        viability_signal: Literal["strong", "moderate", "weak", "unclear"]
        if viability_signal_raw in valid_signals:
            viability_signal = viability_signal_raw  # type: ignore
        else:
            viability_signal = "unclear"

        factors = response.get("factors", [])

        logger.info(
            "Intake scoring complete score=%.1f signal=%s",
            score,
            viability_signal,
        )

        return IntakeResult(
            score=score,
            viability_signal=viability_signal,
            factors=factors,
        )

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Ollama JSON response: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Intake scoring failed: invalid model response",
        )
    except Exception as exc:
        logger.error("Intake scoring failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Intake scoring failed: {str(exc)}",
        )
