"""
HC-6 CDI Agent — direct Python orchestrator
Chains CDI-1 through CDI-6 in sequence.
Can be called directly (tests, Cloud Scheduler sweep) or via ADK.
"""

from __future__ import annotations
import logging

from shared.models import CDIPipelineResult
from agents.cdi.tools.load_encounter import load_encounter_record, summarize_encounter_record
from agents.cdi.tools.extract_diagnoses import extract_coded_diagnoses, format_diagnosis_index_for_log
from agents.cdi.tools.identify_signals import identify_clinical_signals
from agents.cdi.tools.match_gaps import match_signals_to_diagnoses, format_gap_analysis_for_log
from agents.cdi.tools.generate_queries import generate_cdi_queries
from agents.cdi.tools.write_tasks import write_tasks_and_notify

logger = logging.getLogger(__name__)


def run_cdi_pipeline(encounter_id: str) -> CDIPipelineResult:
    """
    Execute the full six-step CDI pipeline for a single encounter.

    CDI-1: Load encounter record from FHIR
    CDI-2: Extract and index coded diagnoses
    CDI-3: Identify clinical signals via Gemini
    CDI-4: Match signals to diagnoses, classify gaps
    CDI-5: Generate CDI physician queries for actionable gaps
    CDI-6: Write FHIR Tasks, BigQuery, Pub/Sub, Firestore
    """
    logger.info(f"CDI pipeline starting — encounter {encounter_id}")

    # CDI-1
    logger.info("CDI-1: Loading encounter record")
    record = load_encounter_record(encounter_id)
    logger.info(f"CDI-1: {summarize_encounter_record(record)}")

    # CDI-2
    logger.info("CDI-2: Extracting coded diagnoses")
    diagnosis_index = extract_coded_diagnoses(record)
    logger.info(format_diagnosis_index_for_log(diagnosis_index))

    # CDI-3
    logger.info("CDI-3: Identifying clinical signals")
    signals = identify_clinical_signals(record)
    logger.info(f"CDI-3: {len(signals)} signals identified")

    if not signals:
        logger.info("CDI-3: No signals found — pipeline complete with no gaps")
        return CDIPipelineResult(
            encounter_id=encounter_id,
            patient_id=record.patient_id,
            signals_found=0,
            gaps_identified=0,
            tasks_written=0,
            task_ids=[],
        )

    # CDI-4
    logger.info("CDI-4: Matching signals to diagnoses")
    gap_analysis = match_signals_to_diagnoses(signals, diagnosis_index, encounter_id)
    logger.info(format_gap_analysis_for_log(gap_analysis))

    if gap_analysis.gaps_above_threshold == 0:
        logger.info("CDI-4: No gaps above confidence threshold — skipping CDI-5 and CDI-6")
        # Still write low-confidence signals to Firestore
        from agents.cdi.tools.write_tasks import write_tasks_and_notify as _write
        result = _write([], gap_analysis, record.patient_id)
        return result

    # CDI-5
    logger.info("CDI-5: Generating CDI physician queries")
    queries = generate_cdi_queries(gap_analysis)
    logger.info(f"CDI-5: {len(queries)} queries generated")

    # CDI-6
    logger.info("CDI-6: Writing FHIR Tasks and notifying")
    result = write_tasks_and_notify(queries, gap_analysis, record.patient_id)

    logger.info(
        f"CDI pipeline complete — encounter {encounter_id} | "
        f"signals={result.signals_found} gaps={result.gaps_identified} "
        f"tasks={result.tasks_written} errors={len(result.errors)}"
    )
    return result


def run_nightly_sweep() -> list[CDIPipelineResult]:
    """
    Nightly sweep mode: load all active inpatient encounters and
    run the CDI pipeline for each one.
    Triggered by Cloud Scheduler via Pub/Sub cdi-trigger topic.
    """
    from shared.fhir_client import list_active_inpatient_encounters

    logger.info("CDI nightly sweep starting")
    encounters = list_active_inpatient_encounters()
    logger.info(f"CDI sweep: {len(encounters)} active inpatient encounters found")

    results: list[CDIPipelineResult] = []
    for enc in encounters:
        encounter_id = enc.get("id")
        if not encounter_id:
            continue
        try:
            result = run_cdi_pipeline(encounter_id)
            results.append(result)
        except Exception as e:
            logger.error(f"CDI sweep: pipeline failed for encounter {encounter_id}: {e}")
            results.append(CDIPipelineResult(
                encounter_id=encounter_id,
                patient_id="unknown",
                signals_found=0,
                gaps_identified=0,
                tasks_written=0,
                task_ids=[],
                errors=[str(e)],
            ))

    total_tasks = sum(r.tasks_written for r in results)
    logger.info(
        f"CDI nightly sweep complete — "
        f"{len(results)} encounters processed, {total_tasks} total tasks written"
    )
    return results
