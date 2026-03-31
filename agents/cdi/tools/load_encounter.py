"""
HC-6 CDI Agent — CDI-1: load_encounter_record
Loads the full inpatient encounter from the FHIR store using $everything.
Returns a structured EncounterRecord with all resource types separated.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from shared.fhir_client import get_encounter_everything, extract_resources_by_type
from shared.config import MAX_OBSERVATIONS


@dataclass
class EncounterRecord:
    encounter_id:        str
    patient_id:          str
    encounter:           dict
    conditions:          list[dict] = field(default_factory=list)
    observations:        list[dict] = field(default_factory=list)
    medication_requests: list[dict] = field(default_factory=list)
    diagnostic_reports:  list[dict] = field(default_factory=list)
    procedures:          list[dict] = field(default_factory=list)
    # All valid FHIR resource IDs in this record — used for hallucination validation in CDI-3
    valid_resource_ids:  set[str]   = field(default_factory=set)


def load_encounter_record(encounter_id: str) -> EncounterRecord:
    """
    CDI-1: Load full encounter using FHIR $everything operation.
    Observations are capped at MAX_OBSERVATIONS (most recent first)
    to keep CDI-3 prompt size manageable.
    """
    bundle = get_encounter_everything(encounter_id)
    entries = bundle.get("entry", [])

    # Extract each resource type
    encounters   = extract_resources_by_type(bundle, "Encounter")
    conditions   = extract_resources_by_type(bundle, "Condition")
    observations = extract_resources_by_type(bundle, "Observation")
    meds         = extract_resources_by_type(bundle, "MedicationRequest")
    reports      = extract_resources_by_type(bundle, "DiagnosticReport")
    procedures   = extract_resources_by_type(bundle, "Procedure")

    if not encounters:
        raise ValueError(f"Encounter/{encounter_id} not found in $everything bundle")

    encounter_resource = encounters[0]

    # Derive patient ID from encounter subject reference
    subject_ref = encounter_resource.get("subject", {}).get("reference", "")
    patient_id = subject_ref.split("/")[-1] if "/" in subject_ref else subject_ref

    # Sort observations by effectiveDateTime descending, cap at MAX_OBSERVATIONS
    observations_sorted = sorted(
        observations,
        key=lambda o: o.get("effectiveDateTime", o.get("effectivePeriod", {}).get("start", "")),
        reverse=True,
    )[:MAX_OBSERVATIONS]

    # Build valid resource ID set for CDI-3 hallucination validation
    valid_ids: set[str] = set()
    for entry in entries:
        resource = entry.get("resource", {})
        rid = resource.get("id")
        if rid:
            valid_ids.add(rid)

    return EncounterRecord(
        encounter_id=encounter_id,
        patient_id=patient_id,
        encounter=encounter_resource,
        conditions=conditions,
        observations=observations_sorted,
        medication_requests=meds,
        diagnostic_reports=reports,
        procedures=procedures,
        valid_resource_ids=valid_ids,
    )


def summarize_encounter_record(record: EncounterRecord) -> str:
    """Return a human-readable summary for logging and ADK trace view."""
    return (
        f"Encounter {record.encounter_id} | Patient {record.patient_id} | "
        f"Conditions: {len(record.conditions)} | "
        f"Observations: {len(record.observations)} | "
        f"Medications: {len(record.medication_requests)} | "
        f"Reports: {len(record.diagnostic_reports)} | "
        f"Procedures: {len(record.procedures)}"
    )
