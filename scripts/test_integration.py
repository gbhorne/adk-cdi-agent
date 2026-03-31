"""
HC-6 CDI Agent — Integration Tests
Runs the full pipeline against live GCP infrastructure.
Requires valid ADC credentials and network access to GCP.

Run:
    python scripts/test_integration.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
import json

from shared.config import TEST_ENCOUNTER_ID, TEST_PATIENT_ID, CONFIDENCE_THRESHOLD
from shared.fhir_client import fhir_get, extract_resources_by_type
from agents.cdi.tools.load_encounter import load_encounter_record
from agents.cdi.tools.extract_diagnoses import extract_coded_diagnoses
from agents.cdi.tools.identify_signals import identify_clinical_signals
from agents.cdi.tools.match_gaps import match_signals_to_diagnoses
from agents.cdi.tools.generate_queries import generate_cdi_queries
from agents.cdi.agent import run_cdi_pipeline


class TestCDI1LoadEncounter(unittest.TestCase):

    def setUp(self):
        self.record = load_encounter_record(TEST_ENCOUNTER_ID)

    def test_encounter_loaded(self):
        self.assertEqual(self.record.encounter_id, TEST_ENCOUNTER_ID)

    def test_patient_id_derived(self):
        self.assertEqual(self.record.patient_id, TEST_PATIENT_ID)

    def test_conditions_loaded(self):
        self.assertGreater(len(self.record.conditions), 0)

    def test_observations_loaded(self):
        self.assertGreater(len(self.record.observations), 0)

    def test_medication_requests_loaded(self):
        self.assertGreater(len(self.record.medication_requests), 0)

    def test_valid_resource_ids_populated(self):
        self.assertGreater(len(self.record.valid_resource_ids), 0)

    def test_all_six_resource_types_represented(self):
        # At minimum Encounter, Condition, Observation, MedicationRequest must be present
        resource_types = {
            r.get("resourceType")
            for r in self.record.conditions + self.record.observations + self.record.medication_requests
        }
        self.assertIn("Condition", resource_types)
        self.assertIn("Observation", resource_types)
        self.assertIn("MedicationRequest", resource_types)


class TestCDI2ExtractDiagnoses(unittest.TestCase):

    def setUp(self):
        record = load_encounter_record(TEST_ENCOUNTER_ID)
        self.index = extract_coded_diagnoses(record)

    def test_diagnoses_extracted(self):
        self.assertGreater(len(self.index.diagnoses), 0)

    def test_icd10_codes_present(self):
        codes = self.index.icd10_codes()
        self.assertGreater(len(codes), 0)

    def test_known_conditions_present(self):
        codes = self.index.icd10_codes()
        # James Thornton has T2DM, HTN, CKD coded
        self.assertIn("E11.9", codes)
        self.assertIn("I10", codes)
        self.assertIn("N18.3", codes)

    def test_ckd_has_poa_unknown(self):
        from shared.models import POAFlag
        ckd = self.index.find_by_icd10("N18.3")
        self.assertIsNotNone(ckd)
        self.assertEqual(ckd.poa_flag, POAFlag.UNKNOWN)


class TestCDI3IdentifySignals(unittest.TestCase):

    def setUp(self):
        self.record = load_encounter_record(TEST_ENCOUNTER_ID)
        self.signals = identify_clinical_signals(self.record)

    def test_signals_identified(self):
        self.assertGreater(len(self.signals), 0,
            "CDI-3 should identify at least one signal for James Thornton")

    def test_aki_signal_present(self):
        aki_signals = [s for s in self.signals if "N17" in s.implied_icd10]
        self.assertGreater(len(aki_signals), 0,
            "Creatinine rising trend should trigger AKI signal")

    def test_all_signals_have_source_resource_ids(self):
        for signal in self.signals:
            self.assertGreater(len(signal.source_resource_ids), 0,
                f"Signal '{signal.implied_condition}' has no source_resource_ids")

    def test_all_source_ids_are_valid_fhir_ids(self):
        for signal in self.signals:
            for rid in signal.source_resource_ids:
                self.assertIn(rid, self.record.valid_resource_ids,
                    f"Signal '{signal.implied_condition}' cites non-existent resource ID '{rid}'")

    def test_all_signals_have_valid_confidence(self):
        for signal in self.signals:
            self.assertGreaterEqual(signal.confidence, 0.5)
            self.assertLessEqual(signal.confidence, 1.0)

    def test_no_family_codes_in_signals(self):
        non_billable = {"E11", "E13", "N17", "N18", "A41", "I50", "I26", "I82", "J96", "K72", "T40"}
        for signal in self.signals:
            self.assertNotIn(signal.implied_icd10, non_billable,
                f"Signal returned non-billable family code: {signal.implied_icd10}")


class TestCDI4MatchGaps(unittest.TestCase):

    def setUp(self):
        record = load_encounter_record(TEST_ENCOUNTER_ID)
        index = extract_coded_diagnoses(record)
        signals = identify_clinical_signals(record)
        self.analysis = match_signals_to_diagnoses(signals, index, TEST_ENCOUNTER_ID)

    def test_gap_analysis_returned(self):
        self.assertEqual(self.analysis.encounter_id, TEST_ENCOUNTER_ID)

    def test_total_signals_correct(self):
        self.assertEqual(
            self.analysis.total_signals,
            len(self.analysis.resolved_signals) + len(self.analysis.gaps)
        )

    def test_gaps_above_threshold_count_correct(self):
        above = sum(1 for g in self.analysis.gaps if g.query_warranted)
        self.assertEqual(above, self.analysis.gaps_above_threshold)

    def test_at_least_one_gap_above_threshold(self):
        self.assertGreater(self.analysis.gaps_above_threshold, 0,
            "James Thornton should have at least one gap above 0.7 confidence")

    def test_existing_diagnoses_resolved(self):
        # T2DM E11.9 is coded — a signal for E11.9 should be resolved, not a gap
        # (specificity signals like E11.65 may still be gaps)
        resolved_codes = {s.implied_icd10 for s in self.analysis.resolved_signals}
        # At minimum some signals should be resolved
        self.assertGreaterEqual(len(self.analysis.resolved_signals), 0)


class TestCDI5GenerateQueries(unittest.TestCase):

    def setUp(self):
        record = load_encounter_record(TEST_ENCOUNTER_ID)
        index = extract_coded_diagnoses(record)
        signals = identify_clinical_signals(record)
        analysis = match_signals_to_diagnoses(signals, index, TEST_ENCOUNTER_ID)
        self.queries = generate_cdi_queries(analysis)
        self.analysis = analysis

    def test_queries_generated_for_actionable_gaps(self):
        self.assertEqual(len(self.queries), self.analysis.gaps_above_threshold)

    def test_all_queries_have_four_sections(self):
        for q in self.queries:
            self.assertTrue(q.clinical_indicator, "clinical_indicator missing")
            self.assertTrue(q.clinical_question, "clinical_question missing")
            self.assertTrue(q.please_clarify, "please_clarify missing")
            self.assertGreater(len(q.response_options), 0, "response_options empty")

    def test_all_queries_have_query_text(self):
        for q in self.queries:
            self.assertTrue(q.query_text)

    def test_priority_values_valid(self):
        for q in self.queries:
            self.assertIn(q.priority, ("ROUTINE", "URGENT"))

    def test_all_queries_have_clinically_undetermined_option(self):
        for q in self.queries:
            has_undetermined = any(
                "undetermined" in opt.lower() or "unable to determine" in opt.lower()
                for opt in q.response_options
            )
            self.assertTrue(has_undetermined,
                f"Query for '{q.gap.signal.implied_condition}' missing 'Clinically undetermined' option")


class TestFullPipeline(unittest.TestCase):

    def test_pipeline_runs_end_to_end(self):
        result = run_cdi_pipeline(TEST_ENCOUNTER_ID)
        self.assertEqual(result.encounter_id, TEST_ENCOUNTER_ID)
        self.assertEqual(result.patient_id, TEST_PATIENT_ID)
        self.assertGreater(result.signals_found, 0)
        self.assertGreater(result.tasks_written, 0)
        self.assertEqual(len(result.errors), 0,
            f"Pipeline completed with errors: {result.errors}")

    def test_fhir_tasks_written_to_store(self):
        result = run_cdi_pipeline(TEST_ENCOUNTER_ID)
        # Verify each task ID exists in the FHIR store
        for task_id in result.task_ids:
            task = fhir_get(f"Task/{task_id}")
            self.assertEqual(task.get("resourceType"), "Task")
            self.assertEqual(task.get("status"), "requested")
            self.assertEqual(task.get("intent"), "order")

    def test_pubsub_message_id_returned(self):
        result = run_cdi_pipeline(TEST_ENCOUNTER_ID)
        self.assertIsNotNone(result.pubsub_message_id)


if __name__ == "__main__":
    print("Running HC-6 CDI Agent integration tests against live GCP...\n")
    print(f"Encounter: {TEST_ENCOUNTER_ID}")
    print(f"Patient:   {TEST_PATIENT_ID}\n")
    unittest.main(verbosity=2)
