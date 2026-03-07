"""
Document classification endpoint.

POST /classify — document type classification.

Uses qwen2.5:1.5b for fast classification.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from integrations.ollama_client import MODEL_CLASSIFIER
from main import app_state
from models.payloads import ClassifyPayload, ClassifyResult

logger = logging.getLogger("pleadly.classify")

router = APIRouter()


@router.post("/classify", response_model=ClassifyResult)
async def classify_document(payload: ClassifyPayload) -> ClassifyResult:
    """
    Classify a document's type based on its extracted text.

    Determines whether a document is a medical record, police report,
    billing statement, imaging report, etc.

    Args:
        payload: ClassifyPayload with document text and firm context.

    Returns:
        ClassifyResult with document type, confidence, and details.
    """
    logger.info(
        "Classifying document doc_id=%s org_id=%s",
        payload.document_id,
        payload.organization_id,
    )

    # Truncate document text to first 2000 chars for classification
    text_sample = payload.document_text[:2000]

    # Build classification prompt
    system_prompt = """You are a legal document classifier. Your task is to classify documents into one of these types:

- medical_record: Clinical notes, treatment records, hospital records, doctor's notes
- police_report: Law enforcement incident reports, accident reports
- billing_statement: Medical bills, invoices, statements of charges
- imaging_report: Radiology reports, MRI reports, CT scan reports, X-ray reports
- pharmacy_record: Prescription records, medication lists
- employment_record: Employment verification, wage statements, personnel files
- insurance_document: Insurance policies, claim documents, correspondence
- legal_correspondence: Demand letters, legal notices, court filings
- other: Any document that doesn't fit the above categories

Respond in JSON format with:
{
  "documentType": "the_type",
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation"
}"""

    user_prompt = f"""/no_think

Document text sample (first 2000 characters):
{text_sample}

Classify this document."""

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
        document_type = response.get("documentType", "other")
        confidence = float(response.get("confidence", 0.0))
        reasoning = response.get("reasoning", "")

        logger.info(
            "Classification complete doc_id=%s type=%s confidence=%.2f",
            payload.document_id,
            document_type,
            confidence,
        )

        return ClassifyResult(
            document_type=document_type,
            confidence=confidence,
            result={
                "documentType": document_type,
                "confidence": confidence,
                "reasoning": reasoning,
            },
        )

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Ollama JSON response: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Classification failed: invalid model response",
        )
    except Exception as exc:
        logger.error("Classification failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Classification failed: {str(exc)}",
        )
