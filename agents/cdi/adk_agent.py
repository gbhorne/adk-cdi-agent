"""
HC-6 CDI Agent — ADK FunctionTool registration
Wraps the six-step CDI pipeline as ADK FunctionTools for the Web UI and trace view.
"""

from __future__ import annotations
import json
import logging

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from shared.config import GEMINI_MODEL, TEST_PATIENT_ID, TEST_ENCOUNTER_ID
from agents.cdi.tools.load_encounter import load_encounter_record, summarize_encounter_record
from agents.cdi.tools.extract_diagnoses import extract_coded_diagnoses, format_diagnosis_index_for_log
from agents.cdi.tools.identify_signals import identify_clinical_signals
from agents.cdi.tools.match_gaps import match_signals_to_diagnoses, format_gap_analysis_for_log
from agents.cdi.tools.generate_queries import generate_cdi_queries
from agents.cdi.tools.write_tasks import write_tasks_and_notify

logger = logging.getLogger(__name__)

# ── ADK tool functions ────────────────────────────────────────────────────────
# Each function is a thin wrapper that accepts primitives (ADK requirement)
# and returns a JSON-serializable string for the trace view.


def tool_load_encounter(encounter_id: str) -> str:
    """
    CDI-1: Load the full inpatient encounter record from the FHIR store.
    Returns a summary of all resource types loaded.

    Args:
        encounter_id: The FHIR Encounter resource ID to load.
    """
    record = load_encounter_record(encounter_id)
    return json.dumps({
        "status": "success",
        "encounter_id": record.encounter_id,
        "patient_id": record.patient_id,
        "summary": summarize_encounter_record(record),
        "resource_counts": {
            "conditions":          len(record.conditions),
            "observations":        len(record.observations),
            "medication_requests": len(record.medication_requests),
            "diagnostic_reports":  len(record.diagnostic_reports),
            "procedures":          len(record.procedures),
        },
    })


def tool_extract_diagnoses(encounter_id: str) -> str:
    """
    CDI-2: Extract and index all coded diagnoses with ICD-10 codes and POA flags.
    Must be called after tool_load_encounter.

    Args:
        encounter_id: The FHIR Encounter resource ID previously loaded.
    """
    record = load_encounter_record(encounter_id)
    index = extract_coded_diagnoses(record)
    return json.dumps({
        "status": "success",
        "encounter_id": index.encounter_id,
        "diagnosis_count": len(index.diagnoses),
        "diagnoses": [
            {
                "icd10": d.icd10_code,
                "description": d.description,
                "poa_flag": d.poa_flag.value,
                "role": d.role.value,
                "status": d.confirmation_status,
            }
            for d in index.diagnoses
        ],
    })


def tool_identify_signals(encounter_id: str) -> str:
    """
    CDI-3: Use Gemini to scan the encounter for clinical signals suggesting
    undocumented or mis-classified diagnoses across five signal categories.

    Args:
        encounter_id: The FHIR Encounter resource ID to analyze.
    """
    record = load_encounter_record(encounter_id)
    signals = identify_clinical_signals(record)
    return json.dumps({
        "status": "success",
        "encounter_id": encounter_id,
        "signal_count": len(signals),
        "signals": [
            {
                "signal_type":        s.signal_type.value,
                "signal_description": s.signal_description,
                "implied_condition":  s.implied_condition,
                "implied_icd10":      s.implied_icd10,
                "confidence":         s.confidence,
                "source_resource_ids": s.source_resource_ids,
            }
            for s in signals
        ],
    })


def tool_match_gaps(encounter_id: str) -> str:
    """
    CDI-4: Cross-reference clinical signals against coded diagnoses.
    Classifies each signal as resolved or a gap, applies 0.7 confidence gate.

    Args:
        encounter_id: The FHIR Encounter resource ID to analyze.
    """
    record = load_encounter_record(encounter_id)
    index = extract_coded_diagnoses(record)
    signals = identify_clinical_signals(record)
    analysis = match_signals_to_diagnoses(signals, index, encounter_id)
    return json.dumps({
        "status": "success",
        "encounter_id": encounter_id,
        "total_signals":        analysis.total_signals,
        "resolved_signals":     len(analysis.resolved_signals),
        "gaps_identified":      len(analysis.gaps),
        "gaps_above_threshold": analysis.gaps_above_threshold,
        "gaps_below_threshold": analysis.gaps_below_threshold,
        "gaps": [
            {
                "gap_type":          g.gap_type.value,
                "implied_condition": g.signal.implied_condition,
                "implied_icd10":     g.signal.implied_icd10,
                "confidence":        g.signal.confidence,
                "query_warranted":   g.query_warranted,
            }
            for g in analysis.gaps
        ],
    })


def tool_generate_queries(encounter_id: str) -> str:
    """
    CDI-5: Generate structured CDI physician queries for all gaps above
    the 0.7 confidence threshold.

    Args:
        encounter_id: The FHIR Encounter resource ID to process.
    """
    record = load_encounter_record(encounter_id)
    index = extract_coded_diagnoses(record)
    signals = identify_clinical_signals(record)
    analysis = match_signals_to_diagnoses(signals, index, encounter_id)
    queries = generate_cdi_queries(analysis)
    return json.dumps({
        "status": "success",
        "encounter_id": encounter_id,
        "query_count": len(queries),
        "queries": [
            {
                "priority":           q.priority,
                "gap_type":           q.gap.gap_type.value,
                "implied_condition":  q.gap.signal.implied_condition,
                "implied_icd10":      q.gap.signal.implied_icd10,
                "clinical_indicator": q.clinical_indicator,
                "clinical_question":  q.clinical_question,
                "response_options":   q.response_options,
            }
            for q in queries
        ],
    })


def tool_write_tasks(encounter_id: str) -> str:
    """
    CDI-6: Write FHIR Task resources for each CDI query, log to BigQuery,
    publish to Pub/Sub, and store low-confidence signals in Firestore.
    This runs the complete pipeline end-to-end.

    Args:
        encounter_id: The FHIR Encounter resource ID to process.
    """
    record = load_encounter_record(encounter_id)
    index = extract_coded_diagnoses(record)
    signals = identify_clinical_signals(record)
    analysis = match_signals_to_diagnoses(signals, index, encounter_id)
    queries = generate_cdi_queries(analysis)
    result = write_tasks_and_notify(queries, analysis, record.patient_id)
    return json.dumps({
        "status":          "success" if not result.errors else "completed_with_errors",
        "encounter_id":    result.encounter_id,
        "patient_id":      result.patient_id,
        "signals_found":   result.signals_found,
        "gaps_identified": result.gaps_identified,
        "tasks_written":   result.tasks_written,
        "task_ids":        result.task_ids,
        "pubsub_message_id": result.pubsub_message_id,
        "errors":          result.errors,
    })


# ── ADK Agent definition ──────────────────────────────────────────────────────

def run_cdi_analysis(encounter_id: str, patient_id: str) -> str:
    """
    Run a complete CDI analysis for the given encounter.
    This is the primary entry point called by the ADK agent.

    Args:
        encounter_id: The FHIR Encounter resource ID.
        patient_id: The FHIR Patient resource ID.
    """
    return tool_write_tasks(encounter_id)


cdi_agent = Agent(
    name="cdi_agent",
    model=GEMINI_MODEL,
    description=(
        "Clinical Documentation Integrity (CDI) Agent. "
        "Analyzes inpatient encounters for documentation gaps, undocumented diagnoses, "
        "and present-on-admission classification issues. "
        "Generates structured CDI physician queries and writes FHIR Task resources."
    ),
    instruction=(
        "You are a CDI agent that analyzes inpatient encounter records. "
        "When asked to run a CDI analysis, use the available tools in order: "
        "first load the encounter, then extract diagnoses, identify signals, "
        "match gaps, generate queries, and finally write the FHIR Tasks. "
        "Report results clearly at each step. "
        f"The test encounter ID is {TEST_ENCOUNTER_ID} and patient ID is {TEST_PATIENT_ID}."
    ),
    tools=[
        FunctionTool(tool_load_encounter),
        FunctionTool(tool_extract_diagnoses),
        FunctionTool(tool_identify_signals),
        FunctionTool(tool_match_gaps),
        FunctionTool(tool_generate_queries),
        FunctionTool(tool_write_tasks),
    ],
)
