"""
HC-6 CDI Agent — FHIR client
Authenticated requests to Google Cloud Healthcare API FHIR R4 store.

Note: Google Cloud Healthcare API does not support $everything on Encounter resources.
Encounter data is loaded via individual resource type searches filtered by encounter reference.
"""

import json
import requests
import google.auth
import google.auth.transport.requests
from typing import Optional
from shared.config import FHIR_BASE_URL, PROJECT_ID, LOCATION, FHIR_DATASET, FHIR_STORE

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _get_headers() -> dict:
    creds, _ = google.auth.default(scopes=_SCOPES)
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/fhir+json",
        "Accept": "application/fhir+json",
    }


def fhir_get(path: str) -> dict:
    """GET a FHIR resource or search result. path is relative to FHIR base URL."""
    url = f"{FHIR_BASE_URL}/{path.lstrip('/')}"
    resp = requests.get(url, headers=_get_headers())
    if not resp.ok:
        raise RuntimeError(f"FHIR GET {path} → {resp.status_code}: {resp.text}")
    return resp.json()


def fhir_post(path: str, body: dict) -> dict:
    """POST to a FHIR endpoint."""
    url = f"{FHIR_BASE_URL}/{path.lstrip('/')}"
    resp = requests.post(url, headers=_get_headers(), json=body)
    if not resp.ok:
        raise RuntimeError(f"FHIR POST {path} → {resp.status_code}: {resp.text}")
    return resp.json()


def fhir_put(resource_type: str, resource_id: str, body: dict) -> dict:
    """PUT (create or update) a FHIR resource."""
    url = f"{FHIR_BASE_URL}/{resource_type}/{resource_id}"
    resp = requests.put(url, headers=_get_headers(), json=body)
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"FHIR PUT {resource_type}/{resource_id} → {resp.status_code}: {resp.text}"
        )
    return resp.json()


def get_encounter_everything(encounter_id: str) -> dict:
    """
    Load all resources linked to an encounter via individual search queries.
    Google Cloud Healthcare API does not support $everything on Encounter resources,
    so we fetch each resource type separately and assemble a synthetic Bundle.
    """
    # First get the Encounter resource itself
    encounter = fhir_get(f"Encounter/{encounter_id}")
    entries = [{"resource": encounter}]

    # Resource types that support encounter search parameter
    encounter_searchable = [
        "Condition",
        "Observation",
        "MedicationRequest",
        "DiagnosticReport",
        "Procedure",
    ]

    for resource_type in encounter_searchable:
        resources = _search_by_encounter(resource_type, encounter_id)
        entries.extend({"resource": r} for r in resources)

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }


def _search_by_encounter(resource_type: str, encounter_id: str) -> list[dict]:
    """Search for resources linked to a specific encounter. Handles pagination."""
    path = f"{resource_type}?encounter=Encounter/{encounter_id}&_count=100"
    try:
        bundle = fhir_get(path)
    except RuntimeError:
        # Some resource types may not support encounter search — skip gracefully
        return []

    all_resources = [
        e["resource"] for e in bundle.get("entry", []) if "resource" in e
    ]

    # Handle pagination
    next_url = _get_next_link(bundle)
    while next_url:
        resp = requests.get(next_url, headers=_get_headers())
        if not resp.ok:
            break
        page = resp.json()
        all_resources.extend(
            e["resource"] for e in page.get("entry", []) if "resource" in e
        )
        next_url = _get_next_link(page)

    return all_resources


def list_active_inpatient_encounters() -> list[dict]:
    """
    Return all active inpatient Encounter resources.
    Used by Cloud Scheduler nightly sweep mode.
    """
    bundle = fhir_get("Encounter?status=in-progress&_count=100")
    entries = bundle.get("entry", [])
    return [e["resource"] for e in entries if "resource" in e]


def write_fhir_task(task_body: dict) -> dict:
    """Write a FHIR Task resource. Returns the created resource with server-assigned ID."""
    url = f"{FHIR_BASE_URL}/Task"
    resp = requests.post(url, headers=_get_headers(), json=task_body)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"FHIR POST Task → {resp.status_code}: {resp.text}")
    return resp.json()


def _get_next_link(bundle: dict) -> Optional[str]:
    """Extract the 'next' pagination link from a FHIR Bundle."""
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            return link.get("url")
    return None


def extract_resources_by_type(bundle: dict, resource_type: str) -> list[dict]:
    """Filter a Bundle's entries by resourceType."""
    return [
        e["resource"]
        for e in bundle.get("entry", [])
        if e.get("resource", {}).get("resourceType") == resource_type
    ]
