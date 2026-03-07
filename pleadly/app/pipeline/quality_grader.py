"""
Quality Grader — 5-dimension scoring rubric for generated legal documents.

Dimensions:
  1. Citation Coverage (35%): % of assertions with mapped fact_id
  2. Numeric & Date Integrity (25%): cross-check all numbers/dates against source
  3. Liability Coherence (20%): DemandPlan elements present in draft
  4. Tone Alignment (10%): style config match via qwen2.5:1.5b classifier
  5. Weakness Coverage (10%): HIGH weakness flags addressed or noted

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


def _score_citation_coverage(
    draft_text: str,
    citations: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    """
    Score citation coverage: what % of factual assertions have a mapped fact_id.

    Returns (score, list_of_unsourced_assertions).
    """
    # Extract sentences that look like factual assertions
    # (sentences with numbers, dates, dollar amounts, or medical terms)
    assertion_patterns = [
        r"[^.]*\$[\d,]+(?:\.\d{2})?[^.]*\.",
        r"[^.]*\d{1,2}/\d{1,2}/\d{2,4}[^.]*\.",
        r"[^.]*(?:diagnosed|treated|surgery|fracture|injury|hospital|emergency)[^.]*\.",
        r"[^.]*(?:plaintiff|defendant|claimant|insured)\s+(?:was|is|had|suffered|sustained)[^.]*\.",
    ]

    assertions: set[str] = set()
    for pattern in assertion_patterns:
        for match in re.finditer(pattern, draft_text, re.IGNORECASE):
            assertions.add(match.group(0).strip())

    if not assertions:
        return 1.0, []

    # Check which assertions have citation backing
    cited_fact_ids = {c.get("fact_id") for c in citations if c.get("fact_id")}
    cited_text_snippets = {
        c.get("source_text", "").lower()[:50]
        for c in citations
        if c.get("source_text")
    }

    unsourced: list[str] = []
    sourced_count = 0

    for assertion in assertions:
        assertion_lower = assertion.lower()
        is_sourced = False

        # Check if any citation text snippet appears in this assertion
        for snippet in cited_text_snippets:
            if snippet and snippet in assertion_lower:
                is_sourced = True
                break

        # Check if assertion references a cited fact_id
        if not is_sourced:
            for fid in cited_fact_ids:
                if fid and fid in assertion:
                    is_sourced = True
                    break

        if is_sourced:
            sourced_count += 1
        else:
            unsourced.append(assertion)

    score = sourced_count / len(assertions) if assertions else 1.0
    return score, unsourced


def _score_numeric_date_integrity(
    draft_text: str,
    source_data: dict[str, Any],
) -> float:
    """
    Score numeric & date integrity: cross-check numbers/dates against source data.

    Extracts dollar amounts and dates from the draft and verifies they appear
    in the source data.
    """
    # Extract dollar amounts from draft
    draft_amounts = set(re.findall(r"\$[\d,]+(?:\.\d{2})?", draft_text))
    # Extract dates from draft
    draft_dates = set(re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", draft_text))

    source_str = json.dumps(source_data)

    if not draft_amounts and not draft_dates:
        return 1.0

    total = len(draft_amounts) + len(draft_dates)
    verified = 0

    for amount in draft_amounts:
        # Normalize and check in source
        normalized = amount.replace(",", "")
        if normalized in source_str or amount in source_str:
            verified += 1

    for d in draft_dates:
        if d in source_str:
            verified += 1

    return verified / total if total > 0 else 1.0


def _score_liability_coherence(
    draft_text: str,
    demand_plan: dict[str, Any],
) -> float:
    """
    Score liability coherence: verify DemandPlan elements are present in draft.
    """
    required_sections = demand_plan.get("required_sections", [])
    if not required_sections:
        # Fall back to checking for standard demand letter sections
        required_sections = [
            "liability",
            "damages",
            "medical",
            "demand",
        ]

    draft_lower = draft_text.lower()
    present = sum(1 for s in required_sections if s.lower() in draft_lower)

    return present / len(required_sections) if required_sections else 1.0


async def _score_tone_alignment(
    draft_text: str,
    target_tone: str,
    ollama: OllamaClient,
) -> float:
    """
    Score tone alignment using qwen2.5:1.5b classifier.

    Sends a snippet of the draft to the classifier model and asks it to
    rate tone alignment on a 0-1 scale.
    """
    # Use first 2000 chars as sample
    sample = draft_text[:2000]

    system_prompt = (
        "You are a legal document tone classifier. "
        "Rate how well the text matches the target tone on a scale of 0.0 to 1.0. "
        "Respond with ONLY a JSON object: {\"score\": <float>, \"reason\": \"<brief>\"}"
    )

    user_prompt = (
        f"Target tone: {target_tone}\n\n"
        f"Text sample:\n{sample}\n\n"
        "Rate the tone alignment (0.0 to 1.0)."
    )

    try:
        result = await ollama.chat_json(
            user_prompt,
            model=MODEL_CLASSIFIER,
            system=system_prompt,
            timeout=30.0,
        )
        score = float(result.get("score", 0.5))
        return max(0.0, min(1.0, score))
    except Exception:
        logger.warning("Tone alignment check failed, defaulting to 0.5")
        return 0.5


def _score_weakness_coverage(
    draft_text: str,
    weakness_flags: list[dict[str, Any]],
) -> float:
    """
    Score weakness coverage: verify HIGH weakness flags are addressed or noted.
    """
    high_flags = [f for f in weakness_flags if f.get("severity") == "HIGH"]

    if not high_flags:
        return 1.0

    draft_lower = draft_text.lower()
    addressed = 0

    for flag in high_flags:
        # Check if the weakness topic is mentioned in the draft
        topic = flag.get("topic", "").lower()
        keywords = flag.get("keywords", [])

        if topic and topic in draft_lower:
            addressed += 1
            continue

        if any(kw.lower() in draft_lower for kw in keywords if kw):
            addressed += 1
            continue

    return addressed / len(high_flags) if high_flags else 1.0


# ---------------------------------------------------------------------------
# Main grading function
# ---------------------------------------------------------------------------


async def grade_document(
    draft_text: str,
    *,
    citations: list[dict[str, Any]] | None = None,
    source_data: dict[str, Any] | None = None,
    demand_plan: dict[str, Any] | None = None,
    target_tone: str = "professional, assertive, empathetic",
    weakness_flags: list[dict[str, Any]] | None = None,
    ollama: OllamaClient | None = None,
) -> GradeResult:
    """
    Grade a generated legal document across 5 dimensions.

    Args:
        draft_text: The generated document text.
        citations: List of citation mappings (fact_id, source_document, etc.).
        source_data: Source data dict for numeric/date cross-checking.
        demand_plan: DemandPlan dict with required sections.
        target_tone: Target tone description for alignment check.
        weakness_flags: List of weakness flags with severity levels.
        ollama: OllamaClient instance for tone checking. If None, a default is created.

    Returns:
        GradeResult with overall score, dimension scores, and delivery decision.
    """
    citations = citations or []
    source_data = source_data or {}
    demand_plan = demand_plan or {}
    weakness_flags = weakness_flags or []

    # 1. Citation Coverage (35%)
    cit_score, unsourced = _score_citation_coverage(draft_text, citations)
    dim_citation = DimensionScore(
        name="Citation Coverage",
        weight=0.35,
        score=cit_score,
        details=f"{len(unsourced)} unsourced assertions found",
    )

    # 2. Numeric & Date Integrity (25%)
    num_score = _score_numeric_date_integrity(draft_text, source_data)
    dim_numeric = DimensionScore(
        name="Numeric & Date Integrity",
        weight=0.25,
        score=num_score,
        details="Cross-checked dollar amounts and dates against source",
    )

    # 3. Liability Coherence (20%)
    lib_score = _score_liability_coherence(draft_text, demand_plan)
    dim_liability = DimensionScore(
        name="Liability Coherence",
        weight=0.20,
        score=lib_score,
        details="Checked required DemandPlan sections in draft",
    )

    # 4. Tone Alignment (10%)
    if ollama is None:
        ollama = OllamaClient()
    try:
        tone_score = await _score_tone_alignment(draft_text, target_tone, ollama)
    except Exception:
        tone_score = 0.5
    dim_tone = DimensionScore(
        name="Tone Alignment",
        weight=0.10,
        score=tone_score,
        details=f"Target tone: {target_tone}",
    )

    # 5. Weakness Coverage (10%)
    weak_score = _score_weakness_coverage(draft_text, weakness_flags)
    dim_weakness = DimensionScore(
        name="Weakness Coverage",
        weight=0.10,
        score=weak_score,
        details=f"{len([f for f in weakness_flags if f.get('severity') == 'HIGH'])} HIGH flags checked",
    )

    dimensions = [dim_citation, dim_numeric, dim_liability, dim_tone, dim_weakness]

    # Calculate weighted overall score
    overall_score = sum(d.weight * d.score for d in dimensions)

    # Delivery decision
    if overall_score >= 0.85 and cit_score >= 0.80:
        delivery_decision = "auto_deliver"
    elif overall_score >= 0.60:
        delivery_decision = "review_required"
    else:
        delivery_decision = "hold"

    return GradeResult(
        overall_score=round(overall_score, 4),
        dimension_scores=dimensions,
        unsourced_assertions=unsourced,
        delivery_decision=delivery_decision,
    )
