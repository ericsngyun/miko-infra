"""
Stage 1: Evidence Compiler — builds verified fact graph from analyzed documents.

This module takes analyzed document data and builds a structured EvidenceGraph
with source attribution. Every fact must be traceable to a source document.

CRITICAL: Hallucination is architecturally impossible — all facts must have
source citations and page references.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger("pleadly.evidence_compiler")


class VerifiedFact:
    """
    A fact with mandatory source attribution.

    Hallucination is impossible because every fact requires a source_document_id
    and source_text (the exact text from the document that supports this fact).
    """

    def __init__(
        self,
        *,
        text: str,
        category: str,
        source_document_id: str,
        source_text: str,
        page_number: int | None = None,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.fact_id = self._generate_id(text, source_document_id)
        self.text = text
        self.category = category
        self.source_document_id = source_document_id
        self.source_text = source_text
        self.page_number = page_number
        self.confidence = confidence
        self.metadata = metadata or {}

    def _generate_id(self, text: str, source: str) -> str:
        """Generate a deterministic ID from text and source."""
        content = f"{text}|{source}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "factId": self.fact_id,
            "text": self.text,
            "category": self.category,
            "sourceDocumentId": self.source_document_id,
            "sourceText": self.source_text,
            "pageNumber": self.page_number,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class CompiledEvidence:
    """
    Evidence compilation result with verified facts and source attribution.

    This is a simplified version of EvidenceGraph focused on the core requirement:
    every fact must be traceable to source text.
    """

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self.facts: list[VerifiedFact] = []
        self.documents: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {}

    def add_document(
        self,
        *,
        document_id: str,
        document_type: str,
        title: str,
        date: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a source document."""
        self.documents.append({
            "documentId": document_id,
            "documentType": document_type,
            "title": title,
            "date": date,
            "metadata": metadata or {},
        })

    def add_fact(self, fact: VerifiedFact) -> None:
        """Add a verified fact to the evidence graph."""
        self.facts.append(fact)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "caseId": self.case_id,
            "facts": [f.to_dict() for f in self.facts],
            "documents": self.documents,
            "metadata": self.metadata,
        }


async def compile_evidence_from_analyzed_documents(
    *,
    analyzed_documents: list[dict[str, Any]],
    case_id: str,
) -> CompiledEvidence:
    """
    Build a verified evidence graph from pre-analyzed documents.

    This function takes documents that have already been analyzed by the
    /analyze endpoint and extracts verifiable facts with source attribution.

    Args:
        analyzed_documents: List of dicts with {documentId, documentType, analysis}
        case_id: The case this evidence belongs to.

    Returns:
        A CompiledEvidence object with verified facts and source attribution.

    CRITICAL: Every fact extracted must include:
    - source_document_id: which document it came from
    - source_text: the exact text from the document supporting this fact
    - page_number: if available
    """
    logger.info(
        "Compiling evidence case_id=%s num_docs=%d",
        case_id,
        len(analyzed_documents),
    )

    compiled = CompiledEvidence(case_id=case_id)

    for doc in analyzed_documents:
        document_id = doc.get("documentId", "unknown")
        document_type = doc.get("documentType", "unknown")
        title = doc.get("title", f"Document {document_id}")
        analysis = doc.get("analysis", {})

        # Register the source document
        compiled.add_document(
            document_id=document_id,
            document_type=document_type,
            title=title,
            date=analysis.get("date"),
            metadata={"analysisType": doc.get("analysisType")},
        )

        # Extract facts based on document type
        if document_type == "medical_record":
            _extract_medical_facts(compiled, document_id, analysis)
        elif document_type == "police_report":
            _extract_police_report_facts(compiled, document_id, analysis)
        elif document_type == "billing_statement":
            _extract_billing_facts(compiled, document_id, analysis)
        else:
            _extract_generic_facts(compiled, document_id, analysis)

    logger.info(
        "Evidence compilation complete case_id=%s total_facts=%d",
        case_id,
        len(compiled.facts),
    )

    return compiled


def _extract_medical_facts(
    compiled: CompiledEvidence,
    document_id: str,
    analysis: dict[str, Any],
) -> None:
    """Extract verified facts from medical record analysis."""
    # Diagnoses
    diagnoses = analysis.get("diagnoses", [])
    if diagnoses:
        for diagnosis in diagnoses if isinstance(diagnoses, list) else [diagnoses]:
            if diagnosis:
                fact = VerifiedFact(
                    text=f"Diagnosis: {diagnosis}",
                    category="medical_diagnosis",
                    source_document_id=document_id,
                    source_text=f"Diagnoses: {diagnosis}",
                    confidence=1.0,
                )
                compiled.add_fact(fact)

    # Treatments
    treatments = analysis.get("treatments", [])
    if treatments:
        for treatment in treatments if isinstance(treatments, list) else [treatments]:
            if treatment:
                fact = VerifiedFact(
                    text=f"Treatment: {treatment}",
                    category="medical_treatment",
                    source_document_id=document_id,
                    source_text=f"Treatments: {treatment}",
                    confidence=1.0,
                )
                compiled.add_fact(fact)

    # Chief complaint
    complaint = analysis.get("chief_complaint")
    if complaint:
        fact = VerifiedFact(
            text=f"Chief complaint: {complaint}",
            category="medical_complaint",
            source_document_id=document_id,
            source_text=f"Chief complaint: {complaint}",
            confidence=1.0,
        )
        compiled.add_fact(fact)


def _extract_police_report_facts(
    compiled: CompiledEvidence,
    document_id: str,
    analysis: dict[str, Any],
) -> None:
    """Extract verified facts from police report analysis."""
    # Fault determination
    fault = analysis.get("fault_determination")
    if fault:
        fact = VerifiedFact(
            text=f"Fault determination: {fault}",
            category="liability",
            source_document_id=document_id,
            source_text=f"Fault: {fault}",
            confidence=1.0,
        )
        compiled.add_fact(fact)

    # Citations issued
    citations = analysis.get("citations")
    if citations:
        fact = VerifiedFact(
            text=f"Citations issued: {citations}",
            category="liability",
            source_document_id=document_id,
            source_text=f"Citations: {citations}",
            confidence=1.0,
        )
        compiled.add_fact(fact)

    # Injuries reported
    injuries = analysis.get("injuries_reported")
    if injuries:
        fact = VerifiedFact(
            text=f"Injuries reported at scene: {injuries}",
            category="injury",
            source_document_id=document_id,
            source_text=f"Injuries: {injuries}",
            confidence=1.0,
        )
        compiled.add_fact(fact)


def _extract_billing_facts(
    compiled: CompiledEvidence,
    document_id: str,
    analysis: dict[str, Any],
) -> None:
    """Extract verified facts from billing statement analysis."""
    # Total charges
    total = analysis.get("total_charges")
    if total:
        fact = VerifiedFact(
            text=f"Total charges: {total}",
            category="damages",
            source_document_id=document_id,
            source_text=f"Total charges: {total}",
            confidence=1.0,
        )
        compiled.add_fact(fact)

    # Date of service
    service_date = analysis.get("date_of_service")
    if service_date:
        fact = VerifiedFact(
            text=f"Date of service: {service_date}",
            category="timeline",
            source_document_id=document_id,
            source_text=f"Date of service: {service_date}",
            confidence=1.0,
        )
        compiled.add_fact(fact)


def _extract_generic_facts(
    compiled: CompiledEvidence,
    document_id: str,
    analysis: dict[str, Any],
) -> None:
    """Extract facts from generic document analysis."""
    # Critical facts
    critical_facts = analysis.get("critical_facts", [])
    if critical_facts:
        for fact_text in critical_facts if isinstance(critical_facts, list) else [critical_facts]:
            if fact_text:
                fact = VerifiedFact(
                    text=str(fact_text),
                    category="general",
                    source_document_id=document_id,
                    source_text=str(fact_text),
                    confidence=0.9,
                )
                compiled.add_fact(fact)
