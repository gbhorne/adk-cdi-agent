from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import json
import logging

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from shared.config import GEMINI_MODEL, TEST_PATIENT_ID, TEST_ENCOUNTER_ID
from agents.cdi.tools.load_encounter import load_encounter_record
from agents.cdi.tools.extract_diagnoses import extract_coded_diagnoses
from agents.cdi.tools.identify_signals import identify_clinical_signals
from agents.cdi.tools.match_gaps import match_signals_to_diagnoses
from agents.cdi.tools.generate_queries import generate_cdi_queries
from agents.cdi.tools.write_tasks import write_tasks_and_notify

logger = logging.getLogger(__name__)


def tool_write_tasks(encounter_id: str) -> str:
    """Run the complete CDI pipeline for an encounter.

    Loads FHIR data, identifies clinical signals, matches documentation
    gaps, generates CDI physician queries, and writes FHIR Task resources.

    Args:
        encounter_id: The FHIR Encounter resource ID to analyze.
    """
    record = load_encounter_record(encounter_id)
    index = extract_coded_diagnoses(record)
    signals = identify_clinical_signals(record)
    analysis = match_signals_to_diagnoses(signals, index, encounter_id)
    queries = generate_cdi_queries(analysis)
    result = write_tasks_and_notify(queries, analysis, record.patient_id)
    return json.dumps({
        "status": "success" if not result.errors else "completed_with_errors",
        "encounter_id": result.encounter_id,
        "patient_id": result.patient_id,
        "signals_found": result.signals_found,
        "gaps_identified": result.gaps_identified,
        "tasks_written": result.tasks_written,
        "task_ids": result.task_ids,
        "pubsub_message_id": result.pubsub_message_id,
        "errors": result.errors,
    })


root_agent = Agent(
    name="cdi_agent",
    model=GEMINI_MODEL,
    description=(
        "Clinical Documentation Integrity Agent. Analyzes inpatient FHIR "
        "encounters for documentation gaps, undocumented diagnoses, and POA "
        "classification issues. Generates CDI physician queries as FHIR Tasks."
    ),
    instruction=(
        "You are a CDI agent. When asked to run a CDI analysis, call "
        "tool_write_tasks with the encounter_id. Report signals found, "
        "gaps identified, and tasks written. "
        "Test encounter: hc6-encounter-thornton-001, "
        "patient: hc6-patient-thornton-001."
    ),
    tools=[FunctionTool(tool_write_tasks)],
)
