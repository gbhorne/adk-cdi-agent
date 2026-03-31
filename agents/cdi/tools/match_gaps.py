"""
HC-6 CDI Agent — CDI-4: match_signals_to_diagnoses
Cross-references ClinicalSignals against the CodedDiagnosisIndex.
Classifies each signal as resolved or a gap, applies confidence gate.
"""

from __future__ import annotations
import logging

from shared.config import CONFIDENCE_THRESHOLD
from shared.models import (
    ClinicalSignal,
    CodedDiagnosisIndex,
    DiagnosisGap,
    GapAnalysis,
    GapType,
    POAFlag,
    SignalType,
)

logger = logging.getLogger(__name__)


def match_signals_to_diagnoses(
    signals: list[ClinicalSignal],
    diagnosis_index: CodedDiagnosisIndex,
    encounter_id: str,
) -> GapAnalysis:
    """
    CDI-4: For each ClinicalSignal, determine whether the implied condition
    is already coded and correctly documented.

    Resolution logic:
    - Signal implied ICD-10 matches a coded diagnosis exactly or at family level
      AND gap type is not POA-related → signal is RESOLVED, no query needed
    - Signal implied ICD-10 matches a coded diagnosis but POA flag is U or W
      AND signal_type is POA_AMBIGUITY → gap type = POA_CLARIFICATION
    - Signal implied ICD-10 matches a coded diagnosis at family level but signal
      suggests a more specific code → gap type = SPECIFICITY_IMPROVEMENT
    - Signal implied ICD-10 has no match in coded diagnoses → gap type = UNDOCUMENTED_CONDITION

    Confidence gate:
    - Gaps with confidence >= CONFIDENCE_THRESHOLD → query_warranted = True (physician-facing)
    - Gaps with confidence < CONFIDENCE_THRESHOLD → query_warranted = False (internal review only)
    """
    resolved: list[ClinicalSignal] = []
    gaps: list[DiagnosisGap] = []

    for signal in signals:
        gap = _classify_signal(signal, diagnosis_index)

        if gap is None:
            # Signal is fully resolved — condition correctly coded
            resolved.append(signal)
            logger.debug(
                f"CDI-4 RESOLVED: {signal.implied_condition} ({signal.implied_icd10}) "
                f"already coded and correctly documented"
            )
        else:
            gaps.append(gap)
            status = "QUERY" if gap.query_warranted else "INTERNAL_REVIEW"
            logger.info(
                f"CDI-4 GAP [{status}]: {signal.implied_condition} ({signal.implied_icd10}) "
                f"type={gap.gap_type.value} confidence={signal.confidence:.2f}"
            )

    gaps_above = sum(1 for g in gaps if g.query_warranted)
    gaps_below = sum(1 for g in gaps if not g.query_warranted)

    return GapAnalysis(
        encounter_id=encounter_id,
        resolved_signals=resolved,
        gaps=gaps,
        total_signals=len(signals),
        gaps_above_threshold=gaps_above,
        gaps_below_threshold=gaps_below,
    )


def _classify_signal(
    signal: ClinicalSignal,
    diagnosis_index: CodedDiagnosisIndex,
) -> DiagnosisGap | None:
    """
    Returns a DiagnosisGap if the signal represents an actionable gap,
    or None if the signal is resolved by existing coding.
    """
    implied_code = signal.implied_icd10.strip()
    matched_diagnosis = diagnosis_index.find_by_icd10(implied_code)

    # ── POA ambiguity signals ─────────────────────────────────────────────────
    if signal.signal_type == SignalType.POA_AMBIGUITY:
        if matched_diagnosis is None:
            # Condition not even coded — bigger gap, treat as undocumented
            return _make_gap(signal, GapType.UNDOCUMENTED_CONDITION)

        if matched_diagnosis.poa_flag in (POAFlag.UNKNOWN, POAFlag.CLINICALLY_UNDETERMINED):
            return _make_gap(signal, GapType.POA_CLARIFICATION)

        # POA flag is already Y or N — resolved
        return None

    # ── No matching diagnosis → undocumented condition ────────────────────────
    if matched_diagnosis is None:
        return _make_gap(signal, GapType.UNDOCUMENTED_CONDITION)

    # ── Exact ICD-10 match → resolved ─────────────────────────────────────────
    if matched_diagnosis.icd10_code == implied_code:
        return None

    # ── Family-level match but different specific code → specificity gap ──────
    # e.g. signal implies E11.65, coded as E11.9 → specificity improvement
    if matched_diagnosis.icd10_code[:3] == implied_code[:3]:
        # Only flag specificity if the implied code is MORE specific than what's coded
        if len(implied_code) > len(matched_diagnosis.icd10_code):
            return _make_gap(signal, GapType.SPECIFICITY_IMPROVEMENT)
        # Coded code is equally or more specific — resolved
        return None

    # ── Different condition family → undocumented ────────────────────────────
    return _make_gap(signal, GapType.UNDOCUMENTED_CONDITION)


def _make_gap(signal: ClinicalSignal, gap_type: GapType) -> DiagnosisGap:
    """Construct a DiagnosisGap, applying the confidence threshold gate."""
    return DiagnosisGap(
        gap_type=gap_type,
        signal=signal,
        query_warranted=signal.confidence >= CONFIDENCE_THRESHOLD,
    )


def format_gap_analysis_for_log(analysis: GapAnalysis) -> str:
    """Human-readable summary for logging and ADK trace view."""
    lines = [
        f"Gap Analysis — Encounter {analysis.encounter_id}",
        f"  Total signals:        {analysis.total_signals}",
        f"  Resolved signals:     {len(analysis.resolved_signals)}",
        f"  Gaps identified:      {len(analysis.gaps)}",
        f"  → Above threshold:    {analysis.gaps_above_threshold} (will generate physician query)",
        f"  → Below threshold:    {analysis.gaps_below_threshold} (internal review only)",
    ]
    for gap in analysis.gaps:
        status = "QUERY ✓" if gap.query_warranted else "INTERNAL"
        lines.append(
            f"  [{status}] {gap.gap_type.value:30s} "
            f"{gap.signal.implied_icd10:10s} {gap.signal.implied_condition} "
            f"(conf={gap.signal.confidence:.2f})"
        )
    return "\n".join(lines)
