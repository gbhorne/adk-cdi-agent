"""
HC-6 CDI Agent — Unit Tests
Tests core pipeline logic without live GCP calls.

Run:
    python scripts/test_unit.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import MagicMock, patch

from shared.models import (
    ClinicalSignal, SignalType, CodedDiagnosis, CodedDiagnosisIndex,
    DiagnosisGap, GapAnalysis, GapType, POAFlag, DiagnosisRole,
)
from shared.config import CONFIDENCE_THRESHOLD
from agents.cdi.tools.extract_diagnoses import (
    _extract_icd10, _extract_poa, _extract_role, _extract_confirmation,
)
from agents.cdi.tools.match_gaps import match_signals_to_diagnoses, _classify_signal


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_signal(icd10: str, confidence: float, signal_type=SignalType.LAB_ABNORMALITY) -> ClinicalSignal:
    return ClinicalSignal(
        signal_type=signal_type,
        signal_description="Test signal",
        source_resource_ids=["test-obs-001"],
        implied_condition="Test condition",
        implied_icd10=icd10,
        confidence=confidence,
    )


def make_diagnosis(icd10: str, poa: POAFlag = POAFlag.YES) -> CodedDiagnosis:
    return CodedDiagnosis(
        condition_id=f"cond-{icd10.replace('.', '')}",
        icd10_code=icd10,
        description=f"Condition {icd10}",
        role=DiagnosisRole.SECONDARY,
        poa_flag=poa,
        confirmation_status="confirmed",
    )


def make_index(*diagnoses: CodedDiagnosis) -> CodedDiagnosisIndex:
    return CodedDiagnosisIndex(
        encounter_id="test-encounter",
        diagnoses=list(diagnoses),
    )


# ── CDI-2 Tests ───────────────────────────────────────────────────────────────

class TestExtractICD10(unittest.TestCase):

    def test_extracts_icd10_from_coding(self):
        condition = {
            "code": {
                "text": "Type 2 diabetes mellitus",
                "coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9", "display": "T2DM"}]
            }
        }
        code, display = _extract_icd10(condition)
        self.assertEqual(code, "E11.9")
        self.assertEqual(display, "T2DM")

    def test_falls_back_to_text_display(self):
        condition = {
            "code": {
                "text": "Hypertension",
                "coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I10", "display": ""}]
            }
        }
        code, display = _extract_icd10(condition)
        self.assertEqual(code, "I10")
        self.assertEqual(display, "Hypertension")

    def test_returns_empty_for_no_coding(self):
        condition = {"code": {"text": "Unknown"}}
        code, display = _extract_icd10(condition)
        self.assertEqual(code, "")


class TestExtractPOA(unittest.TestCase):

    def test_extracts_poa_unknown(self):
        condition = {
            "extension": [{
                "url": "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-present-on-admission",
                "valueCoding": {"system": "https://www.nubc.org/CodeSystem/POAIndicator", "code": "U"}
            }]
        }
        self.assertEqual(_extract_poa(condition), POAFlag.UNKNOWN)

    def test_extracts_poa_yes(self):
        condition = {
            "extension": [{
                "url": "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-present-on-admission",
                "valueCoding": {"code": "Y"}
            }]
        }
        self.assertEqual(_extract_poa(condition), POAFlag.YES)

    def test_defaults_to_unknown_when_no_extension(self):
        condition = {"extension": []}
        self.assertEqual(_extract_poa(condition), POAFlag.UNKNOWN)


# ── Models Tests ──────────────────────────────────────────────────────────────

class TestClinicalSignalValidation(unittest.TestCase):

    def test_valid_signal_passes(self):
        signal = make_signal("N17.9", 0.90)
        self.assertEqual(signal.confidence, 0.90)
        self.assertEqual(signal.implied_icd10, "N17.9")

    def test_confidence_out_of_range_raises(self):
        with self.assertRaises(Exception):
            make_signal("N17.9", 1.5)

    def test_non_billable_family_code_raises(self):
        with self.assertRaises(Exception):
            make_signal("E11", 0.90)

    def test_valid_three_char_billable_code_passes(self):
        # E44 is a valid billable ICD-10-CM code (not in non-billable families)
        signal = make_signal("E44", 0.90)
        self.assertEqual(signal.implied_icd10, "E44")

    def test_icd10_with_decimal_passes(self):
        signal = make_signal("E44.0", 0.90)
        self.assertEqual(signal.implied_icd10, "E44.0")


class TestCodedDiagnosisIndex(unittest.TestCase):

    def setUp(self):
        self.index = make_index(
            make_diagnosis("E11.9"),
            make_diagnosis("I10"),
            make_diagnosis("N18.3", POAFlag.UNKNOWN),
        )

    def test_exact_match(self):
        result = self.index.find_by_icd10("E11.9")
        self.assertIsNotNone(result)
        self.assertEqual(result.icd10_code, "E11.9")

    def test_family_prefix_match(self):
        result = self.index.find_by_icd10("E11.65")
        self.assertIsNotNone(result)
        self.assertEqual(result.icd10_code, "E11.9")

    def test_no_match_returns_none(self):
        result = self.index.find_by_icd10("N17.9")
        self.assertIsNone(result)

    def test_icd10_codes_set(self):
        codes = self.index.icd10_codes()
        self.assertIn("E11.9", codes)
        self.assertIn("I10", codes)


# ── CDI-4 Tests ───────────────────────────────────────────────────────────────

class TestMatchSignalsToDiagnoses(unittest.TestCase):

    def setUp(self):
        self.index = make_index(
            make_diagnosis("E11.9"),
            make_diagnosis("I10"),
            make_diagnosis("N18.3", POAFlag.UNKNOWN),
        )

    def test_exact_match_resolves_signal(self):
        signal = make_signal("E11.9", 0.90)
        gap = _classify_signal(signal, self.index)
        self.assertIsNone(gap)  # resolved

    def test_unmatched_signal_creates_undocumented_gap(self):
        signal = make_signal("N17.9", 0.90)
        gap = _classify_signal(signal, self.index)
        self.assertIsNotNone(gap)
        self.assertEqual(gap.gap_type, GapType.UNDOCUMENTED_CONDITION)

    def test_specificity_gap_detected(self):
        # E11.65 is more specific than coded E11.9
        signal = make_signal("E11.65", 0.80)
        gap = _classify_signal(signal, self.index)
        self.assertIsNotNone(gap)
        self.assertEqual(gap.gap_type, GapType.SPECIFICITY_IMPROVEMENT)

    def test_poa_ambiguity_gap_detected(self):
        # N18.3 coded POA=U — should trigger POA clarification
        signal = ClinicalSignal(
            signal_type=SignalType.POA_AMBIGUITY,
            signal_description="CKD coded POA=U",
            source_resource_ids=["cond-N183"],
            implied_condition="CKD Stage 3",
            implied_icd10="N18.3",
            confidence=0.90,
        )
        gap = _classify_signal(signal, self.index)
        self.assertIsNotNone(gap)
        self.assertEqual(gap.gap_type, GapType.POA_CLARIFICATION)

    def test_confidence_gate_above_threshold(self):
        signal = make_signal("N17.9", CONFIDENCE_THRESHOLD + 0.01)
        gap = _classify_signal(signal, self.index)
        self.assertTrue(gap.query_warranted)

    def test_confidence_gate_below_threshold(self):
        signal = make_signal("N17.9", CONFIDENCE_THRESHOLD - 0.01)
        gap = _classify_signal(signal, self.index)
        self.assertFalse(gap.query_warranted)

    def test_full_gap_analysis_counts(self):
        signals = [
            make_signal("E11.9", 0.90),    # resolved (exact match)
            make_signal("N17.9", 0.90),    # gap above threshold
            make_signal("A41.9", 0.60),    # gap below threshold
        ]
        analysis = match_signals_to_diagnoses(signals, self.index, "test-encounter")
        self.assertEqual(analysis.total_signals, 3)
        self.assertEqual(len(analysis.resolved_signals), 1)
        self.assertEqual(len(analysis.gaps), 2)
        self.assertEqual(analysis.gaps_above_threshold, 1)
        self.assertEqual(analysis.gaps_below_threshold, 1)

    def test_resolved_signals_do_not_generate_gaps(self):
        signals = [make_signal("E11.9", 0.95), make_signal("I10", 0.95)]
        analysis = match_signals_to_diagnoses(signals, self.index, "test-encounter")
        self.assertEqual(len(analysis.gaps), 0)
        self.assertEqual(len(analysis.resolved_signals), 2)


if __name__ == "__main__":
    print("Running HC-6 CDI Agent unit tests...\n")
    unittest.main(verbosity=2)
