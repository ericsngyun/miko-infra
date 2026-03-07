"""
Pydantic v2 models matching the TypeScript interfaces in intelligence.ts EXACTLY.

These models define the contract between the Next.js Control Plane and the
FastAPI Intelligence Plane. Field names use camelCase aliases to match the
JSON wire format from TypeScript.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared context
# ---------------------------------------------------------------------------


class CaseContext(BaseModel):
    """Nested case context within FirmContext."""

    model_config = ConfigDict(populate_by_name=True)

    case_name: str = Field(alias="caseName")
    client_name: str = Field(alias="clientName")
    accident_date: str | None = Field(alias="accidentDate")


class FirmContext(BaseModel):
    """Shared context attached to most requests."""

    model_config = ConfigDict(populate_by_name=True)

    firm_name: str = Field(alias="firmName")
    jurisdiction: str | None = None
    practice_areas: list[str] = Field(alias="practiceAreas")
    case_context: CaseContext | None = Field(default=None, alias="caseContext")


# ---------------------------------------------------------------------------
# Analyze (document analysis)
# ---------------------------------------------------------------------------


class AnalyzePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_text: str = Field(alias="documentText")
    analysis_type: str = Field(alias="analysisType")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")
    document_id: str = Field(alias="documentId")


class AnalyzeResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    result: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------


class ClassifyPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_text: str = Field(alias="documentText")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    document_id: str = Field(alias="documentId")


class ClassifyResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_type: str = Field(alias="documentType")
    confidence: float
    result: dict[str, Any]


# ---------------------------------------------------------------------------
# Cross-analyze
# ---------------------------------------------------------------------------


class CrossAnalyzeDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str
    text: str


class CrossAnalyzePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    documents: list[CrossAnalyzeDocument]
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")


class CrossAnalyzeResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    result: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# Demand letter
# ---------------------------------------------------------------------------


class DemandPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_summary: str = Field(alias="caseSummary")
    medical_summary: str = Field(alias="medicalSummary")
    billing_summary: str = Field(alias="billingSummary")
    police_report: str | None = Field(alias="policeReport")
    demand_amount: float | None = Field(alias="demandAmount")
    multiplier: float | None = None
    instructions: str | None = None
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")


class DemandResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    letter: dict[str, Any]
    metadata: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# Demand section edit
# ---------------------------------------------------------------------------


class DemandEditPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_name: str = Field(alias="sectionName")
    current_content: str = Field(alias="currentContent")
    instructions: str
    additional_context: str | None = Field(alias="additionalContext")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")


class DemandEditResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    edited_section: str = Field(alias="editedSection")
    changes_made: list[str] = Field(alias="changesMade")
    notes_for_attorney: str | None = Field(alias="notesForAttorney")


# ---------------------------------------------------------------------------
# Discovery generation
# ---------------------------------------------------------------------------


class DiscoveryPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_summary: str = Field(alias="caseSummary")
    discovery_type: str = Field(alias="discoveryType")
    target_party: str = Field(alias="targetParty")
    jurisdiction: str | None = None
    specific_topics: list[str] | None = Field(default=None, alias="specificTopics")
    case_type: str = Field(alias="caseType")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    result: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# Discovery review
# ---------------------------------------------------------------------------


class DiscoveryReviewPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    original_requests: str = Field(alias="originalRequests")
    responses: str
    known_facts: str | None = Field(default=None, alias="knownFacts")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")


class DiscoveryReviewResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    result: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# Bill audit
# ---------------------------------------------------------------------------


class BillAuditPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bill_data: str = Field(alias="billData")
    injury_date: str | None = Field(alias="injuryDate")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")
    bill_id: str = Field(alias="billId")


class BillAuditResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    result: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# Lien reduction
# ---------------------------------------------------------------------------


class LienReducePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lien_data: str = Field(alias="lienData")
    settlement_amount: Any = Field(alias="settlementAmount")
    attorney_fees_percentage: Any = Field(alias="attorneyFeesPercentage")
    costs: Any
    jurisdiction: str | None = None
    plaintiff_fault_percentage: Any = Field(alias="plaintiffFaultPercentage")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")
    lien_id: str = Field(alias="lienId")


class LienReduceResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    result: dict[str, Any]
    processing_time_ms: int = Field(alias="processingTimeMs")
    tokens_used: int = Field(alias="tokensUsed")


# ---------------------------------------------------------------------------
# SOL scan
# ---------------------------------------------------------------------------


class SOLPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    jurisdiction: str
    case_type: str = Field(alias="caseType")
    incident_date: str = Field(alias="incidentDate")
    client_dob: str | None = Field(default=None, alias="clientDob")
    defendant_type: str | None = Field(default=None, alias="defendantType")
    is_minor: bool = Field(default=False, alias="isMinor")
    government_entity: bool = Field(default=False, alias="governmentEntity")
    discovery_date: str | None = Field(default=None, alias="discoveryDate")
    additional_facts: str | None = Field(default=None, alias="additionalFacts")


class SOLResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    deadline: str
    statute_cited: str | None = Field(alias="statuteCited")
    sol_period: str | None = Field(alias="solPeriod")
    tolling_applicable: dict[str, Any] | None = Field(alias="tollingApplicable")
    government_tort_notice_deadline: str | None = Field(
        alias="governmentTortNoticeDeadline"
    )
    special_considerations: list[str] = Field(alias="specialConsiderations")
    verify_items: list[str] = Field(alias="verifyItems")
    recommendation: str | None = None
    alert_dates: list[str] = Field(alias="alertDates")
    result: dict[str, Any]


# ---------------------------------------------------------------------------
# Intake scoring
# ---------------------------------------------------------------------------


class IntakePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    caller_name: str | None = Field(alias="callerName")
    accident_type: str | None = Field(alias="accidentType")
    accident_date: str | None = Field(alias="accidentDate")
    injuries_described: str | None = Field(alias="injuriesDescribed")
    sought_medical_treatment: bool | None = Field(alias="soughtMedicalTreatment")
    other_party_at_fault: bool | None = Field(alias="otherPartyAtFault")
    jurisdiction: str | None = None


class IntakeResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    score: float
    viability_signal: Literal["strong", "moderate", "weak", "unclear"] = Field(
        alias="viabilitySignal"
    )
    factors: list[str]


# ---------------------------------------------------------------------------
# Demand rating
# ---------------------------------------------------------------------------


class DemandRatingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    demand_id: str = Field(alias="demandId")
    client_id: str = Field(alias="clientId")
    rating: Literal["accepted_as_is", "minor_edits", "major_edits", "rejected"]
    notes: str | None = None
    attorney_id: str = Field(alias="attorneyId")


# ---------------------------------------------------------------------------
# Store (write content to Intelligence Plane DB)
# ---------------------------------------------------------------------------


class StorePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table: str
    data: dict[str, Any]
    organization_id: str = Field(alias="organizationId")


# ---------------------------------------------------------------------------
# Queue (async job submission)
# ---------------------------------------------------------------------------


class QueuePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_type: str = Field(alias="jobType")
    payload: dict[str, Any]
    organization_id: str = Field(alias="organizationId")


class QueueResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId")
    status: Literal["queued"]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["online", "offline"]
    queue_depth: int = Field(alias="queueDepth")


# ---------------------------------------------------------------------------
# Chronology
# ---------------------------------------------------------------------------


class ChronologyEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: str
    provider: str
    description: str
    document_source: str = Field(alias="documentSource")


class ChronologyGap(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start_date: str = Field(alias="startDate")
    end_date: str = Field(alias="endDate")
    days: int


class ChronologyPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_texts: list[str] = Field(alias="documentTexts")
    firm_context: FirmContext = Field(alias="firmContext")
    organization_id: str = Field(alias="organizationId")
    case_id: str = Field(alias="caseId")


class ChronologyResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entries: list[ChronologyEntry]
    gaps: list[ChronologyGap]
    processing_time_ms: int = Field(alias="processingTimeMs")
