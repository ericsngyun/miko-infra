"""
Document analysis endpoint.

POST /analyze — full document analysis (medical records, police reports, etc.).

Uses qwen3:30b-a3b for deep analysis and extraction.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException

from integrations.ollama_client import MODEL_PRIMARY
from integrations.claude_client import draft_json as claude_draft_json, ClaudeError
from main import app_state
from models.payloads import AnalyzePayload, AnalyzeResult

logger = logging.getLogger("pleadly.analyze")

router = APIRouter()


# Analysis type prompts
ANALYSIS_PROMPTS = {
    "medical_record": """Extract the following from this medical record:
- Patient name and demographics
- Provider name and specialty
- Date(s) of service
- Chief complaint
- Diagnoses (ICD codes if present)
- Treatments and procedures performed
- Medications prescribed
- Follow-up instructions
- Any mentions of causation or work-relatedness
- Imaging or diagnostic tests ordered

Return JSON with these fields.""",

    "police_report": """Extract the following from this police report:
- Report number and date
- Incident date, time, and location
- Involved parties (names, contact info, roles)
- Vehicles involved (make, model, license plates, damage)
- Officer narrative of the incident
- Witness statements
- Citations or violations issued
- Fault determination (if stated)
- Injuries reported
- Diagram or scene description

Return JSON with these fields.""",

    "billing_statement": """Extract the following from this billing statement:
- Provider name and address
- Patient name and account number
- Date(s) of service
- Itemized charges (CPT codes, descriptions, amounts)
- Total charges
- Insurance payments (if any)
- Patient responsibility
- Payment terms

Return JSON with these fields.""",

    "imaging_report": """Extract the following from this imaging report:
- Patient name and demographics
- Exam type (X-ray, MRI, CT, etc.)
- Exam date
- Ordering physician
- Radiologist name
- Clinical indication
- Technique and contrast used
- Findings (detailed)
- Impression/conclusion
- Comparison to prior studies (if mentioned)

Return JSON with these fields.""",

    "full_summary": """Provide a comprehensive analysis of this document:
- Document type
- Key dates
- Involved parties
- Critical facts
- Injuries or damages mentioned
- Liability indicators
- Treatment or services described
- Amounts or financial information

Return JSON with these fields.""",
}


@router.post("/analyze", response_model=AnalyzeResult)
async def analyze_document(payload: AnalyzePayload) -> AnalyzeResult:
    """
    Analyze a document and extract structured information.

    Supports multiple analysis types: full_summary, medical_records,
    police_report, billing_analysis, imaging_analysis, etc.

    Args:
        payload: AnalyzePayload with document text, type, and firm context.

    Returns:
        AnalyzeResult with structured analysis, timing, and token usage.
    """
    start_time = time.time()

    logger.info(
        "Analyzing document doc_id=%s case_id=%s type=%s",
        payload.document_id,
        payload.case_id,
        payload.analysis_type,
    )

    # Select analysis prompt based on type
    analysis_instruction = ANALYSIS_PROMPTS.get(
        payload.analysis_type,
        ANALYSIS_PROMPTS["full_summary"],
    )

    system_prompt = f"""You are a legal document analyzer specializing in personal injury cases.
Your task is to extract structured information from documents accurately and completely.

{analysis_instruction}

Be precise and comprehensive. Include all relevant details. If information is not present, use null.
Never infer facts that aren't explicitly stated in the document."""

    user_prompt = f"""/no_think

Document text:
{payload.document_text}

Analyze this document."""

    try:
        try:
            response_text = await claude_draft_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                task="draft",
                max_tokens=4096,
                temperature=0.1,
            )
            logger.info("Document analysis via Claude API")
        except ClaudeError as claude_err:
            logger.warning("Claude unavailable (%s) — falling back to local", claude_err)
            ollama = app_state["ollama"]
            response_text = await ollama.chat_json(
                prompt=user_prompt,
                model=MODEL_PRIMARY,
                system=system_prompt,
                temperature=0.1,
                timeout=180.0,
            )

        # Calculate timing and token usage (estimate)
        processing_time_ms = int((time.time() - start_time) * 1000)
        tokens_used = len(payload.document_text.split()) + len(str(response_text).split())

        logger.info(
            "Analysis complete doc_id=%s processing_time_ms=%d",
            payload.document_id,
            processing_time_ms,
        )

        return AnalyzeResult(
            result=response_text,
            processing_time_ms=processing_time_ms,
            tokens_used=tokens_used,
        )

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Ollama JSON response: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Analysis failed: invalid model response",
        )
    except Exception as exc:
        logger.error("Analysis failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(exc)}",
        )
