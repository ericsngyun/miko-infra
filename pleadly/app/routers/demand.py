"""
Demand letter generation endpoint.

POST /demand — generates PI demand letter from case summaries.

Uses evidence compiler for fact verification, then qwen3:30b-a3b for drafting.
California jurisdiction focus initially.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException

from integrations.ollama_client import MODEL_PRIMARY
from main import app_state
from models.payloads import DemandPayload, DemandResult

logger = logging.getLogger("pleadly.demand")

router = APIRouter()


@router.post("/demand", response_model=DemandResult)
async def generate_demand(payload: DemandPayload) -> DemandResult:
    """
    Generate a personal injury demand letter.

    This endpoint creates a comprehensive demand letter including:
    - Liability analysis
    - Injuries and treatment summary
    - Special and general damages
    - Demand amount with justification

    Args:
        payload: DemandPayload with case data and firm context.

    Returns:
        DemandResult with generated letter, metadata, and timing.
    """
    start_time = time.time()

    logger.info(
        "Generating demand letter case_id=%s org_id=%s",
        payload.case_id,
        payload.organization_id,
    )

    # Build context for demand letter
    jurisdiction = payload.firm_context.jurisdiction or "California"
    firm_name = payload.firm_context.firm_name or "Our Law Firm"
    case_name = (
        payload.firm_context.case_context.case_name
        if payload.firm_context.case_context
        else "Unknown v. Unknown"
    )
    client_name = (
        payload.firm_context.case_context.client_name
        if payload.firm_context.case_context
        else "Client"
    )
    accident_date = (
        payload.firm_context.case_context.accident_date
        if payload.firm_context.case_context
        else "Unknown"
    )

    # Build comprehensive prompt for demand letter generation
    system_prompt = f"""You are an experienced personal injury attorney drafting a demand letter in {jurisdiction}.

Create a professional demand letter with these sections:

1. INTRODUCTION
   - Introduce yourself and your representation of the plaintiff
   - State the purpose of the letter

2. FACTS AND LIABILITY
   - Chronological narrative of the incident
   - Clear explanation of defendant's negligence
   - Supporting facts from police report (if available)
   - Citation to applicable law showing breach of duty

3. INJURIES AND TREATMENT
   - Description of injuries sustained
   - Medical treatment received (providers, procedures, duration)
   - Current condition and prognosis
   - Permanency of injuries

4. SPECIAL DAMAGES
   - Itemized medical expenses
   - Lost wages (if applicable)
   - Other economic losses
   - Total special damages

5. GENERAL DAMAGES
   - Pain and suffering
   - Emotional distress
   - Loss of enjoyment of life
   - Impact on daily activities

6. DEMAND
   - Total demand amount with breakdown
   - Justification for the amount
   - Deadline for response
   - Consequence of non-response

Use professional, persuasive language. Cite specific facts from the case summaries.
Close the letter with "Sincerely," on its own line, then "[Attorney Name]" on the next line,
then the actual firm name from the FIRM NAME field. Do not write [Your Law Firm] or [Your Contact Information].
Return the letter as JSON with these fields:
{{
  "letterText": "full letter text in markdown format",
  "sections": {{
    "introduction": "text",
    "liability": "text",
    "injuries": "text",
    "specialDamages": "text",
    "generalDamages": "text",
    "demand": "text"
  }},
  "demandAmount": calculated_amount,
  "breakdown": {{
    "specialDamages": amount,
    "generalDamages": amount
  }}
}}"""

    # Prepare case information
    multiplier = payload.multiplier or 3.0
    instructions = payload.instructions or ""

    user_prompt = f"""/no_think

FIRM NAME: {firm_name}
CASE: {case_name}
CLIENT: {client_name}
ACCIDENT DATE: {accident_date}
JURISDICTION: {jurisdiction}

CASE SUMMARY:
{payload.case_summary}

MEDICAL SUMMARY:
{payload.medical_summary}

BILLING SUMMARY:
{payload.billing_summary}

POLICE REPORT:
{payload.police_report or "Not available"}

DEMAND AMOUNT REQUESTED: {payload.demand_amount or "Use multiplier"}
MULTIPLIER: {multiplier}x special damages

SPECIAL INSTRUCTIONS:
{instructions}

Generate a complete, attorney-ready demand letter."""

    try:
        ollama = app_state["ollama"]
        response = await ollama.chat_json(
            prompt=user_prompt,
            model=MODEL_PRIMARY,
            system=system_prompt,
            temperature=0.2,
            timeout=300.0,
        )
        logger.info("Demand letter drafted via local inference case_id=%s", payload.case_id)

        # Calculate processing time and token usage
        processing_time_ms = int((time.time() - start_time) * 1000)
        total_input_length = (
            len(payload.case_summary)
            + len(payload.medical_summary)
            + len(payload.billing_summary)
            + len(payload.police_report or "")
        )
        tokens_used = total_input_length // 4 + len(str(response)) // 4

        logger.info(
            "Demand letter generated case_id=%s processing_time_ms=%d",
            payload.case_id,
            processing_time_ms,
        )

        # Extract letter data
        letter_data = {
            "letterText": response.get("letterText", ""),
            "sections": response.get("sections", {}),
            "demandAmount": response.get("demandAmount"),
            "breakdown": response.get("breakdown", {}),
        }

        metadata = {
            "caseId": payload.case_id,
            "caseName": case_name,
            "clientName": client_name,
            "accidentDate": accident_date,
            "jurisdiction": jurisdiction,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "firmName": payload.firm_context.firm_name,
        }

        return DemandResult(
            letter=letter_data,
            metadata=metadata,
            processing_time_ms=processing_time_ms,
            tokens_used=tokens_used,
        )

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Ollama JSON response: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Demand generation failed: invalid model response",
        )
    except Exception as exc:
        logger.error("Demand generation failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Demand generation failed: {str(exc)}",
        )
