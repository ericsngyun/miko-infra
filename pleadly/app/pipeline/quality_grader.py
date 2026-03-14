"""
Quality Grader — 5-dimension scoring rubric for generated demand letters.

Dimensions:
  1. Liability section present (20%): officer name, VC citations, witness
  2. ICD-10 accuracy (25%): all ICD codes match medical analysis exactly
  3. Billing accuracy (25%): special damages total matches total_billed, CPT line items present
  4. Demand math (15%): total demand = specials + (specials × multiplier)
  5. Tone and completeness (15%): professional language, all 6 sections present

Score caps:
  - If liability section is missing → cap score at 60%
  - If demand math is wrong → deduct 15 points
  - If ICD codes don't match medical_summary → deduct 25 points

Returns overall_score, dimension_scores, unsourced_assertions, delivery_decision.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from integrations.ollama_client import MODEL_CLASSIFIER, OllamaClient

logger = logging.getLogger("pleadly.quality_grader")


@dataclass
class DimensionScore:
    """Score for a single grading dimension."""

    name: str
    weight: float
    score: float  # 0.0 to 1.0
    details: str


@dataclass
class GradeResult:
    """Complete grading result."""

    overall_score: float
    dimension_scores: list[DimensionScore]
    unsourced_assertions: list[str]
    delivery_decision: str  # "auto_deliver" | "review_required" | "hold"


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------


def _score_liability_section(
    draft_text: str,
    police_report: str | None,
) -> tuple[float, str]:
    """
    Score liability section: verify officer name, VC citations, and witnesses are present.

    Returns (score, details).
    """
    draft_lower = draft_text.lower()
    
    # Check for liability section header
    has_liability_section = bool(re.search(r'\bliability\b', draft_lower))
    
    # Extract required elements from police report if available
    has_officer = False
    has_vc_citation = False
    has_witness = False
    
    details_parts = []
    
    # Check for officer name/badge
    officer_patterns = [
        r'officer\s+\w+',
        r'badge\s*#?\s*\d+',
        r'investigating\s+officer',
    ]
    if any(re.search(p, draft_lower) for p in officer_patterns):
        has_officer = True
        details_parts.append("officer present")
    else:
        details_parts.append("officer missing")
    
    # Check for VC (Vehicle Code) citations
    vc_patterns = [
        r'\bvc\s+\d+',
        r'vehicle\s+code\s+(?:section\s+)?\d+',
        r'citation',
    ]
    if any(re.search(p, draft_lower) for p in vc_patterns):
        has_vc_citation = True
        details_parts.append("VC citation present")
    else:
        details_parts.append("VC citation missing")
    
    # Check for witnesses
    witness_patterns = [
        r'\bwitness(?:es)?\b',
        r'independent\s+witness',
        r'bystander',
    ]
    if any(re.search(p, draft_lower) for p in witness_patterns):
        has_witness = True
        details_parts.append("witness present")
    else:
        details_parts.append("witness missing")
    
    # Calculate score: section header (25%) + officer (25%) + VC citation (25%) + witness (25%)
    score = 0.0
    if has_liability_section:
        score += 0.25
    if has_officer:
        score += 0.25
    if has_vc_citation:
        score += 0.25
    if has_witness:
        score += 0.25
    
    details = f"Liability section: {'; '.join(details_parts)}"
    return score, details


def _score_icd10_accuracy(
    draft_text: str,
    medical_summary: str | None,
) -> tuple[float, str]:
    """
    Score ICD-10 accuracy: verify all ICD codes in draft match medical analysis exactly.

    Returns (score, details).
    """
    if not medical_summary:
        return 1.0, "No medical summary provided for ICD-10 verification"
    
    # Extract ICD-10 codes from draft (format: letter followed by digits, optionally with dots)
    draft_icd_pattern = r'\b([A-TV-Z]\d{2}(?:\.\d{1,4})?)\b'
    draft_codes = set(re.findall(draft_icd_pattern, draft_text, re.IGNORECASE))
    
    # Extract ICD-10 codes from medical summary
    source_codes = set(re.findall(draft_icd_pattern, medical_summary, re.IGNORECASE))
    
    if not draft_codes:
        return 1.0, "No ICD-10 codes found in draft"
    
    # Check if all draft codes appear in source
    matching_codes = draft_codes.intersection(source_codes)
    extra_codes = draft_codes - source_codes
    
    if not source_codes:
        # If source has no codes but draft does, that's a problem
        return 0.0, f"Draft has {len(draft_codes)} ICD codes but medical summary has none"
    
    score = len(matching_codes) / len(draft_codes) if draft_codes else 1.0
    
    if extra_codes:
        details = f"{len(matching_codes)}/{len(draft_codes)} ICD codes match; extra codes: {', '.join(sorted(extra_codes))}"
    else:
        details = f"All {len(draft_codes)} ICD-10 codes verified against medical summary"
    
    return score, details


def _score_billing_accuracy(
    draft_text: str,
    billing_summary: str | None,
    source_data: dict[str, Any] | None = None,
) -> tuple[float, str]:
    """
    Score billing accuracy: verify special damages total matches total_billed and CPT codes present.

    Returns (score, details).
    """
    if not billing_summary:
        return 1.0, "No billing summary provided"
    
    # Extract total_billed from source data or billing summary
    total_billed = None
    if source_data:
        total_billed = source_data.get("total_billed") or source_data.get("billingTotal")
    
    # If not in source_data, try to extract from billing_summary
    if total_billed is None and billing_summary:
        # Look for "TOTAL BILLED" line
        total_match = re.search(r'TOTAL\s+BILLED.*?[\$\s]([\d,]+\.?\d*)', billing_summary, re.IGNORECASE)
        if total_match:
            total_billed = float(total_match.group(1).replace(',', ''))
    
    # Extract dollar amounts from draft (special damages section)
    draft_amounts = re.findall(r'\$[\d,]+(?:\.\d{2})?', draft_text)
    
    # Extract CPT codes from draft
    draft_cpt = set(re.findall(r'\bCPT\s+(\d{4,5})\b', draft_text, re.IGNORECASE))
    
    # Extract CPT codes from billing summary
    source_cpt = set(re.findall(r'\bCPT\s+(\d{4,5})\b', billing_summary, re.IGNORECASE))
    
    score_parts = []
    details_parts = []
    
    # Check 1: Total billed amount accuracy (60% of dimension)
    if total_billed is not None:
        # Find the largest amount in draft (likely the total)
        draft_total = None
        for amt_str in draft_amounts:
            amt = float(amt_str.replace('$', '').replace(',', ''))
            if draft_total is None or amt > draft_total:
                draft_total = amt
        
        if draft_total is not None and abs(draft_total - total_billed) < 1.0:
            score_parts.append(0.6)
            details_parts.append(f"total ${draft_total:,.2f} matches source")
        else:
            score_parts.append(0.0)
            details_parts.append(f"total ${draft_total:,.2f} != source ${total_billed:,.2f}" if draft_total else "total not found")
    else:
        score_parts.append(0.6)  # Can't verify, give benefit of doubt
        details_parts.append("total_billed not available for verification")
    
    # Check 2: CPT line items present (40% of dimension)
    if source_cpt:
        matching_cpt = draft_cpt.intersection(source_cpt)
        cpt_score = len(matching_cpt) / len(source_cpt)
        score_parts.append(0.4 * cpt_score)
        details_parts.append(f"{len(matching_cpt)}/{len(source_cpt)} CPT codes present")
    else:
        score_parts.append(0.4)  # No CPT codes in source, give benefit of doubt
        details_parts.append("no CPT codes to verify")
    
    final_score = sum(score_parts)
    details = f"Billing: {'; '.join(details_parts)}"
    
    return final_score, details


def _score_demand_math(
    draft_text: str,
    source_data: dict[str, Any] | None = None,
) -> tuple[float, str]:
    """
    Score demand math: verify total demand = specials + (specials × multiplier).

    Returns (score, details).
    """
    # Extract demand amount from draft (look for final demand line)
    demand_patterns = [
        r'(?:total\s+)?demand(?:\s+amount)?.*?[\$\s]([\d,]+(?:\.\d{2})?)',
        r'settlement.*?[\$\s]([\d,]+(?:\.\d{2})?)',
    ]
    
    demand_amount = None
    for pattern in demand_patterns:
        match = re.search(pattern, draft_text, re.IGNORECASE)
        if match:
            demand_amount = float(match.group(1).replace(',', ''))
            break
    
    # Get specials and multiplier from source_data
    specials = None
    multiplier = None
    
    if source_data:
        specials = source_data.get("total_billed") or source_data.get("billingTotal")
        multiplier = source_data.get("multiplier")
    
    if demand_amount is None:
        return 0.0, "Demand amount not found in draft"
    
    if specials is None or multiplier is None:
        return 1.0, f"Demand amount ${demand_amount:,.2f} (cannot verify: missing specials or multiplier)"
    
    # Calculate expected demand
    expected_demand = specials * (1 + multiplier)
    
    # Allow 1% tolerance for rounding
    tolerance = expected_demand * 0.01
    
    if abs(demand_amount - expected_demand) <= tolerance:
        score = 1.0
        details = f"Demand ${demand_amount:,.2f} = ${specials:,.2f} × {multiplier + 1:.1f} ✓"
    else:
        score = 0.0
        details = f"Demand ${demand_amount:,.2f} ≠ expected ${expected_demand:,.2f} (${specials:,.2f} × {multiplier + 1:.1f})"
    
    return score, details


def _score_tone_and_completeness(
    draft_text: str,
    target_tone: str = "professional, assertive, empathetic",
) -> tuple[float, str]:
    """
    Score tone and completeness: verify professional language and all 6 sections present.

    Expected sections:
    1. Introduction
    2. Facts/Incident
    3. Liability
    4. Injuries/Medical Treatment
    5. Damages/Special Damages
    6. Demand/Settlement

    Returns (score, details).
    """
    draft_lower = draft_text.lower()
    
    # Check for 6 standard demand letter sections
    section_checks = {
        "Introduction": any(re.search(p, draft_lower) for p in [
            r'\bintroduction\b',
            r'\bthis\s+(?:office|firm)\s+represents\b',
            r'\bwe\s+represent\b',
        ]),
        "Facts": any(re.search(p, draft_lower) for p in [
            r'\bfacts\b',
            r'\bincident\b',
            r'\baccident\b.*occurred',
        ]),
        "Liability": re.search(r'\bliability\b', draft_lower) is not None,
        "Medical": any(re.search(p, draft_lower) for p in [
            r'\b(?:injuries|medical)\b',
            r'\btreatment\b',
            r'\bdiagnos(?:is|ed)\b',
        ]),
        "Damages": any(re.search(p, draft_lower) for p in [
            r'\bdamages\b',
            r'\bspecial\s+damages\b',
            r'\bmedical\s+(?:bills|expenses)\b',
        ]),
        "Demand": any(re.search(p, draft_lower) for p in [
            r'\bdemand\b',
            r'\bsettlement\b',
            r'\bresolution\b',
        ]),
    }
    
    present_sections = [name for name, present in section_checks.items() if present]
    section_score = len(present_sections) / 6.0
    
    # Check for unprofessional language (negative signals)
    unprofessional_patterns = [
        r'\bsucks\b',
        r'\bstupid\b',
        r'\bidiot\b',
        r'\!{2,}',  # Multiple exclamation marks
    ]
    
    unprofessional_count = sum(1 for p in unprofessional_patterns if re.search(p, draft_lower))
    tone_penalty = min(0.3, unprofessional_count * 0.1)
    tone_score = max(0.0, 1.0 - tone_penalty)
    
    # Combine: 70% section completeness + 30% tone
    final_score = (0.7 * section_score) + (0.3 * tone_score)
    
    missing = [name for name, present in section_checks.items() if not present]
    details = f"{len(present_sections)}/6 sections present"
    if missing:
        details += f"; missing: {', '.join(missing)}"
    if unprofessional_count > 0:
        details += f"; {unprofessional_count} tone issues"
    
    return final_score, details


# ---------------------------------------------------------------------------
# Main grading function
# ---------------------------------------------------------------------------


async def grade_document(
    draft_text: str,
    *,
    citations: list[dict[str, Any]] | None = None,
    source_data: dict[str, Any] | None = None,
    demand_plan: dict[str, Any] | None = None,
    medical_summary: str | None = None,
    billing_summary: str | None = None,
    police_report: str | None = None,
    target_tone: str = "professional, assertive, empathetic",
    weakness_flags: list[dict[str, Any]] | None = None,
    ollama: OllamaClient | None = None,
) -> GradeResult:
    """
    Grade a generated demand letter across 5 dimensions.

    Args:
        draft_text: The generated document text.
        citations: List of citation mappings (fact_id, source_document, etc.). [DEPRECATED]
        source_data: Source data dict for numeric cross-checking (must include total_billed, multiplier).
        demand_plan: DemandPlan dict with required sections. [DEPRECATED]
        medical_summary: Medical summary text for ICD-10 verification.
        billing_summary: Billing summary text for CPT and total verification.
        police_report: Police report text for liability verification.
        target_tone: Target tone description for alignment check.
        weakness_flags: List of weakness flags with severity levels. [DEPRECATED]
        ollama: OllamaClient instance. [DEPRECATED]

    Returns:
        GradeResult with overall score, dimension scores, and delivery decision.
    """
    source_data = source_data or {}
    
    # 1. Liability section present (20%)
    lib_score, lib_details = _score_liability_section(draft_text, police_report)
    dim_liability = DimensionScore(
        name="Liability Section",
        weight=0.20,
        score=lib_score,
        details=lib_details,
    )
    
    # 2. ICD-10 accuracy (25%)
    icd_score, icd_details = _score_icd10_accuracy(draft_text, medical_summary)
    dim_icd = DimensionScore(
        name="ICD-10 Accuracy",
        weight=0.25,
        score=icd_score,
        details=icd_details,
    )
    
    # 3. Billing accuracy (25%)
    bill_score, bill_details = _score_billing_accuracy(draft_text, billing_summary, source_data)
    dim_billing = DimensionScore(
        name="Billing Accuracy",
        weight=0.25,
        score=bill_score,
        details=bill_details,
    )
    
    # 4. Demand math (15%)
    math_score, math_details = _score_demand_math(draft_text, source_data)
    dim_math = DimensionScore(
        name="Demand Math",
        weight=0.15,
        score=math_score,
        details=math_details,
    )
    
    # 5. Tone and completeness (15%)
    tone_score, tone_details = _score_tone_and_completeness(draft_text, target_tone)
    dim_tone = DimensionScore(
        name="Tone & Completeness",
        weight=0.15,
        score=tone_score,
        details=tone_details,
    )
    
    dimensions = [dim_liability, dim_icd, dim_billing, dim_math, dim_tone]
    
    # Calculate weighted overall score
    overall_score = sum(d.weight * d.score for d in dimensions)
    
    # Apply score caps and deductions
    # If liability section is missing (score < 0.5) → cap at 60%
    if lib_score < 0.5:
        overall_score = min(overall_score, 0.60)
        logger.info(f"Liability section incomplete (score={lib_score:.2f}), capping overall score at 60%")
    
    # If demand math is wrong → deduct 15 points
    if math_score < 0.5:
        overall_score = max(0.0, overall_score - 0.15)
        logger.info(f"Demand math incorrect (score={math_score:.2f}), deducting 15 points")
    
    # If ICD codes don't match medical_summary → deduct 25 points
    if icd_score < 0.8:  # Allow for minor discrepancies
        overall_score = max(0.0, overall_score - 0.25)
        logger.info(f"ICD-10 codes mismatched (score={icd_score:.2f}), deducting 25 points")
    
    # Ensure score stays in valid range
    overall_score = max(0.0, min(1.0, overall_score))
    
    # Delivery decision
    if overall_score >= 0.70:
        delivery_decision = "auto_deliver"
    elif overall_score >= 0.60:
        delivery_decision = "review_required"
    else:
        delivery_decision = "hold"
    
    return GradeResult(
        overall_score=round(overall_score, 4),
        dimension_scores=dimensions,
        unsourced_assertions=[],  # Deprecated, kept for compatibility
        delivery_decision=delivery_decision,
    )
