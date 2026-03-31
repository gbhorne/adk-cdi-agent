"""
HC-6 CDI Agent — BigQuery client
Writes CDI query audit records to cdi_analytics.cdi_queries.
"""

from datetime import datetime, timezone
from typing import Optional
from google.cloud import bigquery
from shared.config import PROJECT_ID, BQ_DATASET, BQ_TABLE, BQ_TABLE_FQN
from shared.models import CDIQuery

_client: Optional[bigquery.Client] = None


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT_ID)
    return _client


def write_cdi_query(
    query: CDIQuery,
    encounter_id: str,
    patient_id: str,
    fhir_task_id: str,
) -> None:
    """
    Insert one CDI query audit row into BigQuery.
    Called synchronously in test; wrap in loop.run_in_executor for production async.
    """
    row = {
        "query_id":          f"{encounter_id}-{fhir_task_id}",
        "encounter_id":      encounter_id,
        "patient_id":        patient_id,
        "query_timestamp":   datetime.now(timezone.utc).isoformat(),
        "gap_type":          query.gap.gap_type.value,
        "signal_type":       query.gap.signal.signal_type.value,
        "implied_condition": query.gap.signal.implied_condition,
        "implied_icd10":     query.gap.signal.implied_icd10,
        "signal_confidence": query.gap.signal.confidence,
        "query_priority":    query.priority,
        "fhir_task_id":      fhir_task_id,
        "physician_response": None,
        "response_timestamp": None,
        "accepted":           None,
    }

    client = _get_client()
    table_ref = f"{PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"
    errors = client.insert_rows_json(table_ref, [row])
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")


def write_cdi_queries_batch(
    queries: list[CDIQuery],
    encounter_id: str,
    patient_id: str,
    task_ids: list[str],
) -> None:
    """Write multiple CDI query rows in a single BigQuery streaming insert."""
    if not queries:
        return

    rows = []
    for query, task_id in zip(queries, task_ids):
        rows.append({
            "query_id":          f"{encounter_id}-{task_id}",
            "encounter_id":      encounter_id,
            "patient_id":        patient_id,
            "query_timestamp":   datetime.now(timezone.utc).isoformat(),
            "gap_type":          query.gap.gap_type.value,
            "signal_type":       query.gap.signal.signal_type.value,
            "implied_condition": query.gap.signal.implied_condition,
            "implied_icd10":     query.gap.signal.implied_icd10,
            "signal_confidence": query.gap.signal.confidence,
            "query_priority":    query.priority,
            "fhir_task_id":      task_id,
            "physician_response": None,
            "response_timestamp": None,
            "accepted":           None,
        })

    client = _get_client()
    table_ref = f"{PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"
    errors = client.insert_rows_json(table_ref, rows)
    if errors:
        raise RuntimeError(f"BigQuery batch insert errors: {errors}")
