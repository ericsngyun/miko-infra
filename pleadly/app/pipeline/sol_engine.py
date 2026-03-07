"""
Deterministic Statute of Limitations (SOL) calculation engine.

Covers: CA, TX, FL, NY, NV, AZ, CO
Includes: General PI, Medical Malpractice, Government Tort Claims,
          Minor Tolling, Discovery Rule Tolling.

This module is fully deterministic — no LLM calls. All deadlines are
computed from codified statutes and input parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# SOL Reference Table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatuteEntry:
    """Statute of limitations entry for a jurisdiction + case type."""

    years: int
    statute: str
    discovery_rule: bool  # Whether discovery rule can extend the deadline
    discovery_max_years: int | None  # Outer cap for discovery rule (from incident)


@dataclass(frozen=True)
class GovTortEntry:
    """Government tort claim filing deadline."""

    days: int
    statute: str
    notes: str


@dataclass(frozen=True)
class MinorTollingRule:
    """Tolling rule for minors."""

    toll_until_age: int  # SOL doesn't begin until this age
    max_years_from_incident: int | None  # Outer cap even with tolling
    statute: str


# General PI SOL by state
GENERAL_PI_SOL: dict[str, StatuteEntry] = {
    "CA": StatuteEntry(2, "Cal. Code Civ. Proc. \u00a7 335.1", True, 3),
    "TX": StatuteEntry(2, "Tex. Civ. Prac. & Rem. Code \u00a7 16.003", True, None),
    "FL": StatuteEntry(2, "Fla. Stat. \u00a7 95.11(3)(a) (as amended by HB 837, eff. 3/24/2023)", True, 4),
    "NY": StatuteEntry(3, "N.Y. CPLR \u00a7 214(5)", True, None),
    "NV": StatuteEntry(2, "Nev. Rev. Stat. \u00a7 11.190(4)(e)", True, None),
    "AZ": StatuteEntry(2, "Ariz. Rev. Stat. \u00a7 12-542", True, None),
    "CO": StatuteEntry(2, "Colo. Rev. Stat. \u00a7 13-80-102(1)(a)", True, 3),
}

# Medical malpractice SOL by state
MED_MAL_SOL: dict[str, StatuteEntry] = {
    "CA": StatuteEntry(1, "Cal. Code Civ. Proc. \u00a7 340.5", True, 3),
    "TX": StatuteEntry(2, "Tex. Civ. Prac. & Rem. Code \u00a7 74.251", True, 10),
    "FL": StatuteEntry(2, "Fla. Stat. \u00a7 95.11(4)(b)", True, 4),
    "NY": StatuteEntry(2, "N.Y. CPLR \u00a7 214-a", True, None),  # 2.5 years actually
    "NV": StatuteEntry(1, "Nev. Rev. Stat. \u00a7 41A.097", True, 3),
    "AZ": StatuteEntry(2, "Ariz. Rev. Stat. \u00a7 12-542", True, None),
    "CO": StatuteEntry(2, "Colo. Rev. Stat. \u00a7 13-80-102.5", True, 3),
}

# Government tort claim deadlines by state
GOV_TORT_CLAIMS: dict[str, GovTortEntry] = {
    "CA": GovTortEntry(180, "Cal. Gov. Code \u00a7 911.2", "Must file claim within 6 months of accrual"),
    "TX": GovTortEntry(180, "Tex. Civ. Prac. & Rem. Code \u00a7 101.101", "Must give notice within 6 months"),
    "FL": GovTortEntry(180, "Fla. Stat. \u00a7 768.28(6)(a)", "Must give written notice within 180 days"),
    "NY": GovTortEntry(90, "N.Y. Gen. Mun. Law \u00a7 50-e", "Must file notice of claim within 90 days"),
    "NV": GovTortEntry(180, "Nev. Rev. Stat. \u00a7 41.036", "Must file claim within 180 days"),  # 2 years for lawsuit
    "AZ": GovTortEntry(180, "Ariz. Rev. Stat. \u00a7 12-821.01", "Must file claim within 180 days"),
    "CO": GovTortEntry(182, "Colo. Rev. Stat. \u00a7 24-10-109", "Must file notice within 182 days"),
}

# Minor tolling rules by state
MINOR_TOLLING: dict[str, MinorTollingRule] = {
    "CA": MinorTollingRule(18, 8, "Cal. Code Civ. Proc. \u00a7 352(a)"),
    "TX": MinorTollingRule(18, None, "Tex. Civ. Prac. & Rem. Code \u00a7 16.001"),
    "FL": MinorTollingRule(18, 7, "Fla. Stat. \u00a7 95.051(1)(h)"),
    "NY": MinorTollingRule(18, None, "N.Y. CPLR \u00a7 208"),
    "NV": MinorTollingRule(18, None, "Nev. Rev. Stat. \u00a7 11.250"),
    "AZ": MinorTollingRule(18, None, "Ariz. Rev. Stat. \u00a7 12-502"),
    "CO": MinorTollingRule(18, None, "Colo. Rev. Stat. \u00a7 13-81-103"),
}

# Standard alert thresholds (days before deadline)
ALERT_THRESHOLDS = [180, 90, 60, 30, 14, 7]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _parse_date(date_str: str) -> date:
    """Parse an ISO date string (YYYY-MM-DD) into a date object."""
    return date.fromisoformat(date_str)


def _age_at_date(dob: date, at_date: date) -> int:
    """Calculate age in years at a given date."""
    age = at_date.year - dob.year
    if (at_date.month, at_date.day) < (dob.month, dob.day):
        age -= 1
    return age


def _date_when_age(dob: date, age: int) -> date:
    """Calculate the date when a person reaches a given age."""
    return dob.replace(year=dob.year + age)


# ---------------------------------------------------------------------------
# Main calculation
# ---------------------------------------------------------------------------


@dataclass
class SOLCalculation:
    """Result of a SOL calculation."""

    deadline: date
    statute_cited: str
    tolling_applied: dict[str, Any]
    alert_dates: list[date]
    sol_period: str
    government_tort_notice_deadline: date | None
    special_considerations: list[str]
    verify_items: list[str]
    recommendation: str | None


def calculate_sol(
    *,
    jurisdiction: str,
    case_type: str,
    incident_date: str,
    client_dob: str | None = None,
    defendant_type: str | None = None,
    is_minor: bool = False,
    government_entity: bool = False,
    discovery_date: str | None = None,
    additional_facts: str | None = None,
) -> SOLCalculation:
    """
    Calculate statute of limitations deadline for the given parameters.

    Args:
        jurisdiction: Two-letter state code (CA, TX, FL, NY, NV, AZ, CO).
        case_type: Type of case (e.g., 'general_pi', 'medical_malpractice', 'auto_accident').
        incident_date: ISO date string of the incident (YYYY-MM-DD).
        client_dob: ISO date string of client's date of birth, if known.
        defendant_type: Type of defendant ('individual', 'government', 'corporate', etc.).
        is_minor: Whether the client was a minor at time of incident.
        government_entity: Whether a government entity is the defendant.
        discovery_date: ISO date when injury/cause was discovered, if different from incident.
        additional_facts: Free-text additional facts (logged as metadata only).

    Returns:
        SOLCalculation with deadline, statute, tolling info, and alert dates.

    Raises:
        ValueError: If jurisdiction is not supported or dates are invalid.
    """
    state = jurisdiction.upper().strip()
    if state not in GENERAL_PI_SOL:
        raise ValueError(
            f"Unsupported jurisdiction: {jurisdiction}. "
            f"Supported: {', '.join(sorted(GENERAL_PI_SOL.keys()))}"
        )

    incident = _parse_date(incident_date)
    today = date.today()
    tolling_info: dict[str, Any] = {}
    special_considerations: list[str] = []
    verify_items: list[str] = []

    # Select the appropriate SOL table
    is_med_mal = case_type.lower() in ("medical_malpractice", "med_mal", "medmal")
    sol_table = MED_MAL_SOL if is_med_mal else GENERAL_PI_SOL
    entry = sol_table[state]

    # Base deadline: incident_date + SOL years
    accrual_date = incident
    statute_cited = entry.statute
    sol_years = entry.years

    # Discovery rule tolling
    if discovery_date and entry.discovery_rule:
        discovery = _parse_date(discovery_date)
        if discovery > incident:
            # Deadline runs from discovery date instead of incident date
            accrual_date = discovery
            tolling_info["discovery_rule"] = {
                "applied": True,
                "discovery_date": discovery_date,
                "original_accrual": incident_date,
            }
            special_considerations.append(
                f"Discovery rule applied: SOL runs from discovery date ({discovery_date}) "
                f"instead of incident date ({incident_date})."
            )

            # Check outer cap if applicable
            if entry.discovery_max_years:
                outer_cap = incident + timedelta(days=entry.discovery_max_years * 365)
                tolling_info["discovery_rule"]["outer_cap"] = outer_cap.isoformat()
                special_considerations.append(
                    f"Discovery rule outer cap: {entry.discovery_max_years} years from "
                    f"incident ({outer_cap.isoformat()})."
                )

            verify_items.append(
                "Verify discovery date with client — must establish when injury "
                "or its cause was actually discovered or should have been discovered."
            )

    # Calculate base deadline
    base_deadline = accrual_date + timedelta(days=sol_years * 365)

    # Apply discovery rule outer cap if it exists and is earlier
    if (
        discovery_date
        and entry.discovery_rule
        and entry.discovery_max_years
    ):
        outer_cap = incident + timedelta(days=entry.discovery_max_years * 365)
        if outer_cap < base_deadline:
            base_deadline = outer_cap
            special_considerations.append(
                "Outer cap on discovery rule is the controlling deadline."
            )

    # Minor tolling
    dob: date | None = None
    if client_dob:
        dob = _parse_date(client_dob)

    if is_minor or (dob and _age_at_date(dob, incident) < 18):
        if dob is None:
            verify_items.append(
                "Client indicated as minor but DOB not provided. "
                "Cannot calculate tolled deadline without DOB."
            )
            special_considerations.append(
                "Minor tolling may apply but DOB is required for calculation."
            )
        else:
            minor_rule = MINOR_TOLLING[state]
            age_at_incident = _age_at_date(dob, incident)
            if age_at_incident < minor_rule.toll_until_age:
                # SOL tolled until minor reaches the specified age
                toll_end = _date_when_age(dob, minor_rule.toll_until_age)
                tolled_deadline = toll_end + timedelta(days=sol_years * 365)

                tolling_info["minor_tolling"] = {
                    "applied": True,
                    "age_at_incident": age_at_incident,
                    "toll_until_age": minor_rule.toll_until_age,
                    "toll_end_date": toll_end.isoformat(),
                    "statute": minor_rule.statute,
                }

                # Check outer cap for minor tolling
                if minor_rule.max_years_from_incident:
                    minor_cap = incident + timedelta(
                        days=minor_rule.max_years_from_incident * 365
                    )
                    if minor_cap < tolled_deadline:
                        tolled_deadline = minor_cap
                        tolling_info["minor_tolling"]["outer_cap"] = (
                            minor_cap.isoformat()
                        )
                        special_considerations.append(
                            f"Minor tolling outer cap: {minor_rule.max_years_from_incident} "
                            f"years from incident ({minor_cap.isoformat()})."
                        )

                if tolled_deadline > base_deadline:
                    base_deadline = tolled_deadline
                    statute_cited = f"{entry.statute}; {minor_rule.statute}"
                    special_considerations.append(
                        f"Minor tolling applied: client was {age_at_incident} at time of "
                        f"incident. SOL tolled until age {minor_rule.toll_until_age} "
                        f"({toll_end.isoformat()})."
                    )

    # Government tort claim deadline
    gov_deadline: date | None = None
    if government_entity or (defendant_type and "gov" in defendant_type.lower()):
        if state in GOV_TORT_CLAIMS:
            gov_entry = GOV_TORT_CLAIMS[state]
            gov_deadline = incident + timedelta(days=gov_entry.days)

            special_considerations.append(
                f"GOVERNMENT ENTITY: Must file tort claim notice within "
                f"{gov_entry.days} days of incident ({gov_deadline.isoformat()}). "
                f"{gov_entry.notes}. Cite: {gov_entry.statute}."
            )
            statute_cited = f"{statute_cited}; {gov_entry.statute}"

            if gov_deadline < today:
                special_considerations.append(
                    "WARNING: Government tort claim notice deadline may have already passed. "
                    "Review immediately for late claim filing options."
                )
            verify_items.append(
                "Confirm government entity status and identify the specific agency/entity."
            )

    # Sol period description
    sol_period = f"{sol_years} year{'s' if sol_years != 1 else ''}"

    # Generate alert dates
    alert_dates: list[date] = []
    for threshold_days in ALERT_THRESHOLDS:
        alert_date = base_deadline - timedelta(days=threshold_days)
        if alert_date > today:
            alert_dates.append(alert_date)

    # Also add gov deadline alerts if applicable
    if gov_deadline and gov_deadline > today:
        for threshold_days in ALERT_THRESHOLDS:
            gov_alert = gov_deadline - timedelta(days=threshold_days)
            if gov_alert > today and gov_alert not in alert_dates:
                alert_dates.append(gov_alert)

    alert_dates.sort()

    # Recommendation
    days_remaining = (base_deadline - today).days
    if days_remaining < 0:
        recommendation = (
            "CRITICAL: The statute of limitations appears to have expired. "
            "Consult with supervising attorney immediately regarding any "
            "tolling arguments or exceptions that may apply."
        )
    elif days_remaining <= 30:
        recommendation = (
            "URGENT: Less than 30 days remain. File immediately or seek "
            "a tolling agreement."
        )
    elif days_remaining <= 90:
        recommendation = (
            "Filing deadline approaching. Begin final preparation of complaint."
        )
    elif days_remaining <= 180:
        recommendation = (
            "Monitor deadline. Ensure all discovery and investigation is complete "
            "well before the filing deadline."
        )
    else:
        recommendation = None

    # Standard verify items
    verify_items.append(
        "Confirm incident date and jurisdiction with client and case file."
    )
    if is_med_mal:
        verify_items.append(
            "Medical malpractice: verify whether pre-suit notice or "
            "expert affidavit requirements apply in this jurisdiction."
        )

    return SOLCalculation(
        deadline=base_deadline,
        statute_cited=statute_cited,
        tolling_applied=tolling_info,
        alert_dates=alert_dates,
        sol_period=sol_period,
        government_tort_notice_deadline=gov_deadline,
        special_considerations=special_considerations,
        verify_items=verify_items,
        recommendation=recommendation,
    )
