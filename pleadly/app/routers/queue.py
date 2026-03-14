"""
Async demand queue endpoint.
POST /demand/queue — accepts flat QueueDemandPayload, returns jobId immediately.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from integrations.ollama_client import MODEL_PRIMARY
from main import app_state, settings
from models.payloads import QueueResult

logger = logging.getLogger("pleadly.queue")

router = APIRouter()


# ---------------------------------------------------------------------------
# Flat inbound model (matches QueueDemandPayload in intelligence.ts)
# ---------------------------------------------------------------------------

class FirmCaseContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    case_name: str | None = Field(None, alias="caseName")
    client_name: str | None = Field(None, alias="clientName")
    accident_date: str | None = Field(None, alias="accidentDate")


class FirmContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_name: str = Field("Unknown Firm", alias="firmName")
    jurisdiction: str | None = Field(None, alias="jurisdiction")
    practice_areas: list[str] = Field(default_factory=list, alias="practiceAreas")
    case_context: FirmCaseContext | None = Field(None, alias="caseContext")


class QueueDemandPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")
    demand_status_id: str = Field(alias="demandStatusId")
    case_summary: str = Field("", alias="caseSummary")
    medical_summary: str = Field("", alias="medicalSummary")
    billing_summary: str = Field("", alias="billingSummary")
    police_report: str | None = Field(None, alias="policeReport")
    firm_context: FirmContext = Field(default_factory=FirmContext, alias="firmContext")
    demand_amount: float | None = Field(None, alias="demandAmount")
    multiplier: float | None = Field(None, alias="multiplier")
    attorney_instructions: str | None = Field(None, alias="attorneyInstructions")


# ---------------------------------------------------------------------------
# Callback helper
# ---------------------------------------------------------------------------

async def post_callback(payload: dict[str, Any], settings: Any) -> None:
    callback_url = getattr(settings, "pleadly_callback_url", "")
    hmac_secret = getattr(settings, "pleadly_hmac_secret", "")

    if not callback_url or not hmac_secret:
        logger.error("Callback URL or HMAC secret not configured")
        return

    url = f"{callback_url}/api/intelligence/callback"
    body = json.dumps(payload)
    timestamp = str(int(time.time()))
    message = f"{timestamp}.{body}"
    signature = hmac.new(
        hmac_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Pleadly-Timestamp": timestamp,
        "X-Pleadly-Signature": signature,
    }

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, content=body, headers=headers)
                if resp.status_code < 300:
                    logger.info("Callback sent jobType=%s status=%d", payload.get("jobType"), resp.status_code)
                    return
                logger.warning("Callback failed attempt=%d status=%d body=%s", attempt + 1, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Callback error attempt=%d error=%s", attempt + 1, exc)
        await asyncio.sleep(2 ** attempt)

    logger.error("Callback failed after 3 attempts jobType=%s", payload.get("jobType"))


# ---------------------------------------------------------------------------
# Pipeline background task
# ---------------------------------------------------------------------------

async def run_demand_pipeline(
    job_id: str,
    p: QueueDemandPayload,
    settings: Any,
) -> None:
    async def cb(job_type: str, result: dict[str, Any], status: str = "success") -> None:
        await post_callback({
            "jobType": job_type,
            "jobId": job_id,
            "demandStatusId": p.demand_status_id,
            "caseId": p.case_id,
            "organizationId": p.organization_id,
            "status": status,
            "result": result,
        }, settings)

    try:
        ollama = app_state["ollama"]
        firm = p.firm_context
        case_ctx = firm.case_context
        jurisdiction = firm.jurisdiction or "California"
        firm_name = firm.firm_name
        client_name = case_ctx.client_name if case_ctx else "the Plaintiff"
        case_name = case_ctx.case_name if case_ctx else "Matter"
        multiplier = p.multiplier or 3.0

        # Stage 1 — planning
        await cb("demand-draft-phase-1", {
            "planJson": {"sections": ["introduction", "facts", "injuries", "damages", "demand"]},
            "pipelineStatus": "drafting",
        })

        system_prompt = f"""You are a California personal injury attorney drafting a formal settlement demand letter. Today's date is {__import__('datetime').date.today().strftime('%B %d, %Y')}. The 30-day response deadline is {(__import__('datetime').date.today() + __import__('datetime').timedelta(days=30)).strftime('%B %d, %Y')}.

Draft a professional demand letter in six clearly labeled sections. Use the client's full name throughout — never write "Client" or "Plaintiff".

SECTION 1 — REPRESENTATION AND PURPOSE
Identify the firm by name, state you represent the client by full name, reference the date and location of loss, and state this letter constitutes a formal demand for settlement of all personal injury claims arising from the incident.

SECTION 2 — LIABILITY NARRATIVE  
Describe the accident mechanism using only the facts provided. Identify the specific negligent act. Cite California Civil Code § 1714(a) for the negligence standard. For rear-end collisions, note the presumption of negligence. For intersection collisions, cite CVC § 21800. State that the defendant's negligence was the sole proximate cause of all injuries.

SECTION 3 — MEDICAL TREATMENT CHRONOLOGY
Present treatment in strict chronological order. For each provider: name, dates of service, specific diagnoses using clinical terminology, diagnostic findings verbatim from imaging reports (MRI findings, X-ray results), treatment rendered, current status, and prognosis. Include all ICD-10 codes if available. End with current functional limitations.

SECTION 4 — SPECIAL DAMAGES ITEMIZATION
List every billing line item with: date of service, CPT code, service description, provider name, and exact dollar amount. Subtotal by provider. State the grand total in bold. Note lien status if applicable. Note if account is held pending legal disposition. Never estimate or approximate — use only figures from billing records.

SECTION 5 — GENERAL DAMAGES
Describe pain and suffering tied to specific clinical findings. Reference the ROM limitations by exact degree measurements. Describe emotional distress from the collision mechanism. Describe loss of enjoyment of life with specific activities impacted. If applicable, note loss of consortium and loss of earning capacity.

SECTION 6 — SETTLEMENT DEMAND
State: (1) total special damages, (2) general damages multiplier with justification referencing specific injuries, (3) general damages amount (specials × multiplier), (4) total demand (specials + general damages) in bold. Then state: "We demand payment of $[TOTAL] on or before [30-DAY DATE]. This demand will expire without further notice on that date." Include a request for policy limits disclosure. Include lien preservation notice if medical bills are under lien.

Close with:
Sincerely,

{firm_name}

CRITICAL RULES — NEVER VIOLATE:
- Never write [Insert X], [Amount], [Date], or any placeholder — omit the sentence entirely if data is missing
- Never use "Client" or "Plaintiff" — use the client's full name
- All dollar amounts must come exclusively from the billing records provided
- The demand total must equal specials + (specials × multiplier) — show the arithmetic explicitly
- Write the actual 30-day deadline date, calculated from today
- Reference imaging findings using the exact clinical language from the medical records
- Professional, formal tone — no hedging, no qualifications

Return ONLY valid JSON with no markdown fences:
{{
  "letterText": "full letter in plain text with section headers",
  "sections": {{
    "representation": "section 1 text",
    "liability": "section 2 text", 
    "treatment": "section 3 text",
    "special_damages": "section 4 text",
    "general_damages": "section 5 text",
    "demand": "section 6 text"
  }},
  "metadata": {{
    "demandAmount": <total demand as number>,
    "multiplierUsed": <multiplier as number>,
    "totalMedicalSpecials": <total specials as number>,
    "deadlineDate": "<30-day deadline as string>"
  }}
}}"""

        demand_str = f"${p.demand_amount:,.2f}" if p.demand_amount else "to be calculated from specials"

        user_prompt = f"""/no_think

FIRM: {firm_name}
CLIENT: {client_name}
CASE: {case_name}
JURISDICTION: {jurisdiction}
DEMAND: {demand_str}
MULTIPLIER: {multiplier}x

CASE SUMMARY:
{p.case_summary or "Not provided"}

MEDICAL RECORDS:
{p.medical_summary or "Not provided"}

BILLING RECORDS:
{p.billing_summary or "Not provided"}

POLICE REPORT:
{p.police_report or "Not available"}

Generate a complete attorney-ready demand letter using ONLY the facts above."""

        raw = await ollama.chat(
            prompt=user_prompt,
            model=MODEL_PRIMARY,
            system=system_prompt,
            temperature=0.2,
            timeout=300.0,
        )

        cleaned = re.sub(r"```json|```", "", raw).strip()
        response = json.loads(cleaned)

        letter_text = response.get("letterText", "")
        sections = response.get("sections", {})
        metadata = response.get("metadata", {})
        preview_text = letter_text[:500] if letter_text else ""

        # Stage 2 — draft complete
        await cb("demand-draft-phase-2", {
            "sections": sections,
            "previewText": preview_text,
            "letterText": letter_text,
            "pipelineStatus": "qa_review",
        })

        # Stage 3 — QA grading
        from pipeline.quality_grader import grade_document
        
        # Extract total_billed from billing_summary for source_data
        import re as re_extract
        total_billed = None
        if p.billing_summary:
            total_match = re_extract.search(r'TOTAL\s+BILLED.*?[\$\s]([\d,]+\.?\d*)', p.billing_summary, re_extract.IGNORECASE)
            if total_match:
                total_billed = float(total_match.group(1).replace(',', ''))
        
        grade = await grade_document(
            letter_text,
            source_data={
                "total_billed": total_billed,
                "multiplier": p.multiplier,
            },
            medical_summary=p.medical_summary,
            billing_summary=p.billing_summary,
            police_report=p.police_report,
        )

        unsupported = [a[:100] for a in grade.unsourced_assertions[:5]]

        await cb("demand-qa", {
            "confidenceScore": int(grade.overall_score * 100),
            "unsupportedClaims": unsupported,
            "qaIterationCount": 1,
            "deliveryDecision": grade.delivery_decision,
            "pipelineStatus": "qa_review",
        })

        # Stage 4 — ready
        gen_sig = hmac.new(
            getattr(settings, "pleadly_hmac_secret", "").encode(),
            f"{p.demand_status_id}.{int(time.time())}".encode(),
            hashlib.sha256,
        ).hexdigest()

        await cb("demand-ready", {
            "generatedBy": "evo-x2",
            "modelVersion": "Qwen3.5-35B-A3B",
            "generationSignature": gen_sig,
            "demandSections": sections,
            "letterText": letter_text,
            "metadata": metadata,
            "pipelineStatus": "ready_for_review",
        })

        logger.info("Demand pipeline complete job_id=%s", job_id)

    except Exception as exc:
        logger.exception("Demand pipeline failed job_id=%s", job_id)
        await cb("demand-draft-phase-1", {
            "error": str(exc),
            "pipelineStatus": "pending",
        })


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/demand/queue", response_model=QueueResult)
async def submit_demand_job(
    payload: QueueDemandPayload,
    background_tasks: BackgroundTasks,
) -> QueueResult:
    job_id = str(uuid.uuid4())
    pass  # settings imported from main

    logger.info(
        "Queued demand job job_id=%s org_id=%s demand_status_id=%s",
        job_id,
        payload.organization_id,
        payload.demand_status_id,
    )

    background_tasks.add_task(run_demand_pipeline, job_id, payload, settings)

    return QueueResult(jobId=job_id, status="queued")
