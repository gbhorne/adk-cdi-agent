"""
Run this script to write agents/cdi/agent.py and agents/cdi/adk_agent.py
with correct UTF-8 encoding and proper sys.path injection for ADK web.

Usage:
    python scripts/write_adk_files.py
"""
import pathlib

ROOT = pathlib.Path(__file__).parent.parent

AGENT_PY = '''\
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import json
import logging

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from shared.config import GEMINI_MODEL, TEST_PATIENT_ID, TEST_ENCOUNTER_ID
from shared.models import CDIPipelineResult
from agents.cdi.tools.load_encounter import load_encounter_record, summarize_encounter_record
from agents.cdi.tools.extract_diagnoses import extract_coded_diagnoses, format_diagnosis_index_for_log
from agents.cdi.tools.identify_signals import identify_clinical_signals
from agents.cdi.tools.match_gaps import match_signals_to_diagnoses, format_gap_analysis_for_log
from agents.cdi.tools.generate_queries import generate_cdi_queries
from agents.cdi.tools.write_tasks import write_tasks_and_notify

logger = logging.getLogger(__name__)


def run_cdi_pipeline(encounter_id: str) -> CDIPipelineResult:
    logger.info(f"CDI pipeline starting: encounter {encounter_id}")
    record = load_encounter_record(encounter_id)
    logger.info(f"CDI-1: {summarize_encounter_record(record)}")
    diagnosis_index = extract_coded_diagnoses(record)
    logger.info(format_diagnosis_index_for_log(diagnosis_index))
    signals = identify_clinical_signals(record)
    logger.info(f"CDI-3: {len(signals)} signals identified")
    if not signals:
        return CDIPipelineResult(
            encounter_id=encounter_id,
            patient_id=record.patient_id,
            signals_found=0, gaps_identified=0, tasks_written=0, task_ids=[],
        )
    gap_analysis = match_signals_to_diagnoses(signals, diagnosis_index, encounter_id)
    logger.info(format_gap_analysis_for_log(gap_analysis))
    if gap_analysis.gaps_above_threshold == 0:
        return write_tasks_and_notify([], gap_analysis, record.patient_id)
    queries = generate_cdi_queries(gap_analysis)
    result = write_tasks_and_notify(queries, gap_analysis, record.patient_id)
    logger.info(
        f"CDI pipeline complete: signals={result.signals_found} "
        f"gaps={result.gaps_identified} tasks={result.tasks_written}"
    )
    return result


def run_nightly_sweep() -> list[CDIPipelineResult]:
    from shared.fhir_client import list_active_inpatient_encounters
    encounters = list_active_inpatient_encounters()
    results = []
    for enc in encounters:
        encounter_id = enc.get("id")
        if not encounter_id:
            continue
        try:
            results.append(run_cdi_pipeline(encounter_id))
        except Exception as e:
            logger.error(f"sweep failed for {encounter_id}: {e}")
            results.append(CDIPipelineResult(
                encounter_id=encounter_id, patient_id="unknown",
                signals_found=0, gaps_identified=0, tasks_written=0,
                task_ids=[], errors=[str(e)],
            ))
    return results


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
'''

agent_path = ROOT / "agents" / "cdi" / "agent.py"
agent_path.write_text(AGENT_PY, encoding="utf-8")
print(f"Written: {agent_path}")
print("Done -- restart adk web agents")
