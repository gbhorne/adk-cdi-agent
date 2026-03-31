"""
HC-6 CDI Agent — CDI-6: write_tasks_and_notify
For each CDIQuery, writes a FHIR Task resource, logs to BigQuery,
publishes to Pub/Sub, and stores low-confidence signals in Firestore.
"""

from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from google.cloud import pubsub_v1
from google.cloud import firestore

from shared.config import (
    PROJECT_ID,
    PUBSUB_NOTIFY_TOPIC,
    FS_PROCESSING_COLLECTION,
    FS_HISTORY_COLLECTION,
    TASK_RESPONSE_WINDOW_HRS,
)
from shared.models import CDIQuery, GapAnalysis, CDIPipelineResult
from shared.fhir_client import write_fhir_task
from shared.bigquery_client import write_cdi_queries_batch

logger = logging.getLogger(__name__)

# LOINC code for CDI physician query
_CDI_QUERY_LOINC = "74468-0"
_CDI_QUERY_LOINC_DISPLAY = "Questionnaire Form Definition Document"


def write_tasks_and_notify(
    queries: list[CDIQuery],
    gap_analysis: GapAnalysis,
    patient_id: str,
) -> CDIPipelineResult:
    """
    CDI-6: Write FHIR Tasks, audit to BigQuery, notify via Pub/Sub,
    and archive low-confidence gaps to Firestore.

    Returns a CDIPipelineResult summarizing the full pipeline run.
    """
    encounter_id = gap_analysis.encounter_id
    task_ids: list[str] = []
    errors: list[str] = []

    # ── 1. Write FHIR Task for each query ────────────────────────────────────
    for query in queries:
        try:
            task_resource = _build_fhir_task(query, encounter_id, patient_id)
            created = write_fhir_task(task_resource)
            task_id = created.get("id", task_resource["id"])
            task_ids.append(task_id)
            logger.info(
                f"CDI-6: FHIR Task/{task_id} written — "
                f"{query.gap.signal.implied_condition} [{query.priority}]"
            )
        except Exception as e:
            msg = f"FHIR Task write failed for '{query.gap.signal.implied_condition}': {e}"
            logger.error(f"CDI-6: {msg}")
            errors.append(msg)

    # ── 2. BigQuery audit log ─────────────────────────────────────────────────
    if queries and task_ids:
        try:
            write_cdi_queries_batch(
                queries=queries[:len(task_ids)],
                encounter_id=encounter_id,
                patient_id=patient_id,
                task_ids=task_ids,
            )
            logger.info(f"CDI-6: {len(task_ids)} rows written to BigQuery")
        except Exception as e:
            msg = f"BigQuery write failed: {e}"
            logger.error(f"CDI-6: {msg}")
            errors.append(msg)

    # ── 3. Firestore — low-confidence signals for internal review ─────────────
    low_confidence_gaps = [g for g in gap_analysis.gaps if not g.query_warranted]
    if low_confidence_gaps:
        _write_low_confidence_to_firestore(low_confidence_gaps, encounter_id, patient_id)

    # ── 4. Firestore — processing state update ────────────────────────────────
    _update_processing_state(encounter_id, patient_id, len(task_ids), errors)

    # ── 5. Pub/Sub notification ───────────────────────────────────────────────
    pubsub_message_id = None
    if task_ids:
        try:
            pubsub_message_id = _publish_review_ready(
                encounter_id=encounter_id,
                patient_id=patient_id,
                task_ids=task_ids,
                gap_count=gap_analysis.gaps_above_threshold,
            )
            logger.info(f"CDI-6: Pub/Sub message {pubsub_message_id} published to {PUBSUB_NOTIFY_TOPIC}")
        except Exception as e:
            msg = f"Pub/Sub publish failed: {e}"
            logger.error(f"CDI-6: {msg}")
            errors.append(msg)

    return CDIPipelineResult(
        encounter_id=encounter_id,
        patient_id=patient_id,
        signals_found=gap_analysis.total_signals,
        gaps_identified=len(gap_analysis.gaps),
        tasks_written=len(task_ids),
        task_ids=task_ids,
        pubsub_message_id=pubsub_message_id,
        errors=errors,
    )


def _build_fhir_task(
    query: CDIQuery,
    encounter_id: str,
    patient_id: str,
) -> dict:
    """Build a FHIR Task resource body for a CDI physician query."""
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=TASK_RESPONSE_WINDOW_HRS)
    task_id = f"cdi-task-{uuid.uuid4().hex[:12]}"

    return {
        "resourceType": "Task",
        "id": task_id,
        "status": "requested",
        "intent": "order",
        "priority": query.priority.lower(),
        "code": {
            "coding": [{
                "system": "http://loinc.org",
                "code": _CDI_QUERY_LOINC,
                "display": _CDI_QUERY_LOINC_DISPLAY,
            }],
            "text": "CDI Physician Query",
        },
        "description": query.query_text,
        "focus": {
            "reference": f"Encounter/{encounter_id}",
        },
        "for": {
            "reference": f"Patient/{patient_id}",
        },
        "authoredOn": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastModified": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "restriction": {
            "period": {
                "end": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        },
        "note": [{
            "text": (
                f"Gap type: {query.gap.gap_type.value} | "
                f"Signal type: {query.gap.signal.signal_type.value} | "
                f"ICD-10: {query.gap.signal.implied_icd10} | "
                f"Confidence: {query.gap.signal.confidence:.2f}"
            )
        }],
    }


def _publish_review_ready(
    encounter_id: str,
    patient_id: str,
    task_ids: list[str],
    gap_count: int,
) -> str:
    """Publish a summary message to the cdi-review-ready Pub/Sub topic."""
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_NOTIFY_TOPIC)

    message_data = {
        "encounter_id": encounter_id,
        "patient_id": patient_id,
        "task_ids": task_ids,
        "gap_count": gap_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "hc6-cdi-agent",
    }

    future = publisher.publish(
        topic_path,
        data=json.dumps(message_data).encode("utf-8"),
        encounter_id=encounter_id,
        gap_count=str(gap_count),
    )
    return future.result(timeout=10)


def _write_low_confidence_to_firestore(
    gaps: list,
    encounter_id: str,
    patient_id: str,
) -> None:
    """
    Write low-confidence gaps to Firestore cdi_query_history collection.
    These are for internal CDI specialist review only — no physician query generated.
    """
    db = firestore.Client(project=PROJECT_ID)
    batch = db.batch()

    for gap in gaps:
        doc_ref = db.collection(FS_HISTORY_COLLECTION).document(
            f"{encounter_id}-{gap.signal.implied_icd10}-{uuid.uuid4().hex[:8]}"
        )
        batch.set(doc_ref, {
            "encounter_id":       encounter_id,
            "patient_id":         patient_id,
            "gap_type":           gap.gap_type.value,
            "signal_type":        gap.signal.signal_type.value,
            "signal_description": gap.signal.signal_description,
            "implied_condition":  gap.signal.implied_condition,
            "implied_icd10":      gap.signal.implied_icd10,
            "confidence":         gap.signal.confidence,
            "source_resource_ids": gap.signal.source_resource_ids,
            "created_at":         datetime.now(timezone.utc).isoformat(),
            "status":             "pending_internal_review",
            "query_warranted":    False,
        })

    batch.commit()
    logger.info(
        f"CDI-6: {len(gaps)} low-confidence gaps written to Firestore/{FS_HISTORY_COLLECTION}"
    )


def _update_processing_state(
    encounter_id: str,
    patient_id: str,
    tasks_written: int,
    errors: list[str],
) -> None:
    """Update per-encounter processing state in Firestore."""
    db = firestore.Client(project=PROJECT_ID)
    doc_ref = db.collection(FS_PROCESSING_COLLECTION).document(encounter_id)
    doc_ref.set({
        "encounter_id":  encounter_id,
        "patient_id":    patient_id,
        "last_run":      datetime.now(timezone.utc).isoformat(),
        "tasks_written": tasks_written,
        "status":        "completed" if not errors else "completed_with_errors",
        "errors":        errors,
    }, merge=True)
