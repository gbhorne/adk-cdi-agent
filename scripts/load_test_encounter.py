"""
HC-6 CDI Agent — Load James Thornton synthetic FHIR data
Loads all resources needed for CDI signal detection testing.

Run from adk-cdi-agent root:
    python scripts/load_test_encounter.py
"""

import json
import requests
import google.auth
import google.auth.transport.requests
from datetime import datetime, timezone, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ID   = "healthcare-sa-2026"
LOCATION     = "us-central1"
DATASET      = "healthcare-dataset"
FHIR_STORE   = "cdss-fhir-store"
BASE_URL     = (
    f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}"
    f"/locations/{LOCATION}/datasets/{DATASET}/fhirStores/{FHIR_STORE}/fhir"
)

# Stable IDs — these become the canonical HC-6 test constants
PATIENT_ID   = "hc6-patient-thornton-001"
ENCOUNTER_ID = "hc6-encounter-thornton-001"

# Timestamps relative to "admission" 72 hours ago
NOW          = datetime.now(timezone.utc)
ADMIT_TIME   = NOW - timedelta(hours=72)
H24          = ADMIT_TIME + timedelta(hours=24)
H48          = ADMIT_TIME + timedelta(hours=48)
H60          = ADMIT_TIME + timedelta(hours=60)

def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_headers() -> dict:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/fhir+json",
    }

# ── FHIR helpers ──────────────────────────────────────────────────────────────
def put_resource(resource_type: str, resource_id: str, body: dict) -> dict:
    url = f"{BASE_URL}/{resource_type}/{resource_id}"
    resp = requests.put(url, headers=get_headers(), json=body)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"PUT {resource_type}/{resource_id} → {resp.status_code}: {resp.text}")
    print(f"  ✓ {resource_type}/{resource_id}")
    return resp.json()

# ── Resources ─────────────────────────────────────────────────────────────────

def patient() -> dict:
    return {
        "resourceType": "Patient",
        "id": PATIENT_ID,
        "name": [{"use": "official", "family": "Thornton", "given": ["James"]}],
        "gender": "male",
        "birthDate": "1958-04-12",
        "identifier": [{"system": "urn:oid:2.16.840.1.113883.4.1", "value": "999-00-0001"}],
    }

def encounter() -> dict:
    return {
        "resourceType": "Encounter",
        "id": ENCOUNTER_ID,
        "status": "in-progress",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP", "display": "inpatient encounter"},
        "type": [{"coding": [{"system": "http://snomed.info/sct", "code": "32485007", "display": "Hospital admission"}]}],
        "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "period": {"start": ts(ADMIT_TIME)},
        "reasonCode": [{"coding": [{"system": "http://snomed.info/sct", "code": "73211009", "display": "Diabetes mellitus"}]}],
        "hospitalization": {"dischargeDisposition": {"coding": [{"code": "home", "display": "Home"}]}},
    }

def conditions() -> list:
    """
    Coded diagnoses — deliberately sparse to give CDI-3 room to find gaps.
    T2DM coded at family level E11.9 (not specific) — CDI should flag specificity.
    CKD coded POA=U (unknown) — CDI should flag for POA clarification.
    HTN coded correctly as baseline.
    """
    return [
        {
            "resourceType": "Condition",
            "id": "hc6-cond-dm-001",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]}],
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9", "display": "Type 2 diabetes mellitus without complications"}], "text": "Type 2 diabetes mellitus"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "onsetDateTime": "2015-01-01",
            "extension": [{"url": "https://hl7.org/fhir/us/core/StructureDefinition/us-core-condition-assertedDate", "valueDateTime": ts(ADMIT_TIME)}],
        },
        {
            "resourceType": "Condition",
            "id": "hc6-cond-htn-001",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]}],
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I10", "display": "Essential (primary) hypertension"}], "text": "Hypertension"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "onsetDateTime": "2012-06-01",
        },
        {
            "resourceType": "Condition",
            "id": "hc6-cond-ckd-001",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]}],
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "N18.3", "display": "Chronic kidney disease, stage 3"}], "text": "CKD Stage 3"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "onsetDateTime": "2019-03-15",
            # POA=U intentionally — CDI should query for clarification
            "extension": [{"url": "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-present-on-admission", "valueCoding": {"system": "https://www.nubc.org/CodeSystem/POAIndicator", "code": "U", "display": "Unknown"}}],
        },
    ]

def observations() -> list:
    """
    Labs and vitals designed to trigger specific CDI signals:
    - HbA1c 8.4% → DM specificity signal (E11.65 glycemic control)
    - Creatinine rising trend (1.1 → 1.4 → 1.8) → AKI signal N17.9
    - Glucose 287 mg/dL → hyperglycemia signal R73.09
    - WBC 14,200 → SIRS/infection signal
    - Albumin 2.7 g/dL → malnutrition signal E44
    """
    obs = [
        # HbA1c
        {
            "resourceType": "Observation",
            "id": "hc6-obs-hba1c-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4", "display": "Hemoglobin A1c/Hemoglobin.total in Blood"}], "text": "HbA1c"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 8.4, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"},
        },
        # Creatinine — 3 readings showing upward trend (AKI signal)
        {
            "resourceType": "Observation",
            "id": "hc6-obs-creat-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "2160-0", "display": "Creatinine [Mass/volume] in Serum or Plasma"}], "text": "Creatinine"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(ADMIT_TIME),
            "valueQuantity": {"value": 1.1, "unit": "mg/dL", "system": "http://unitsofmeasure.org", "code": "mg/dL"},
        },
        {
            "resourceType": "Observation",
            "id": "hc6-obs-creat-002",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "2160-0", "display": "Creatinine [Mass/volume] in Serum or Plasma"}], "text": "Creatinine"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 1.4, "unit": "mg/dL", "system": "http://unitsofmeasure.org", "code": "mg/dL"},
        },
        {
            "resourceType": "Observation",
            "id": "hc6-obs-creat-003",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "2160-0", "display": "Creatinine [Mass/volume] in Serum or Plasma"}], "text": "Creatinine"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H48),
            "valueQuantity": {"value": 1.8, "unit": "mg/dL", "system": "http://unitsofmeasure.org", "code": "mg/dL"},
        },
        # Glucose — elevated, hyperglycemia signal
        {
            "resourceType": "Observation",
            "id": "hc6-obs-glucose-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7", "display": "Glucose [Mass/volume] in Serum or Plasma"}], "text": "Glucose"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 287, "unit": "mg/dL", "system": "http://unitsofmeasure.org", "code": "mg/dL"},
        },
        # WBC — elevated, SIRS signal
        {
            "resourceType": "Observation",
            "id": "hc6-obs-wbc-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "6690-2", "display": "Leukocytes [#/volume] in Blood by Automated count"}], "text": "WBC"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 14200, "unit": "/uL", "system": "http://unitsofmeasure.org", "code": "/uL"},
        },
        # Albumin — low, malnutrition signal
        {
            "resourceType": "Observation",
            "id": "hc6-obs-albumin-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "1751-7", "display": "Albumin [Mass/volume] in Serum or Plasma"}], "text": "Albumin"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 2.7, "unit": "g/dL", "system": "http://unitsofmeasure.org", "code": "g/dL"},
        },
        # Vitals — HR and RR normal, SpO2 normal (no false positive signals)
        {
            "resourceType": "Observation",
            "id": "hc6-obs-hr-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}], "text": "Heart Rate"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 88, "unit": "/min", "system": "http://unitsofmeasure.org", "code": "/min"},
        },
        {
            "resourceType": "Observation",
            "id": "hc6-obs-spo2-001",
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "59408-5", "display": "Oxygen saturation in Arterial blood by Pulse oximetry"}], "text": "SpO2"},
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "effectiveDateTime": ts(H24),
            "valueQuantity": {"value": 97, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"},
        },
    ]
    return obs

def medication_requests() -> list:
    """
    Insulin drip → DKA/hyperglycemic crisis signal (E11.10 / E13.00)
    Metformin → consistent with T2DM, no additional signal
    Lisinopril → consistent with HTN, no additional signal
    """
    return [
        {
            "resourceType": "MedicationRequest",
            "id": "hc6-med-insulin-drip-001",
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {
                "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "349498", "display": "insulin regular, human 100 UNT/ML Injectable Solution"}],
                "text": "Insulin Regular (Human) IV Infusion (Insulin Drip)"
            },
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "authoredOn": ts(H24),
            "dosageInstruction": [{"text": "0.1 units/kg/hr IV infusion, titrate per protocol", "route": {"coding": [{"system": "http://snomed.info/sct", "code": "47625008", "display": "Intravenous route"}]}}],
        },
        {
            "resourceType": "MedicationRequest",
            "id": "hc6-med-metformin-001",
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {
                "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "860975", "display": "Metformin hydrochloride 500 MG Oral Tablet"}],
                "text": "Metformin 500mg PO BID"
            },
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "authoredOn": ts(ADMIT_TIME),
            "dosageInstruction": [{"text": "500 mg by mouth twice daily"}],
        },
        {
            "resourceType": "MedicationRequest",
            "id": "hc6-med-lisinopril-001",
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {
                "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "104375", "display": "Lisinopril 10 MG Oral Tablet"}],
                "text": "Lisinopril 10mg PO daily"
            },
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "authoredOn": ts(ADMIT_TIME),
            "dosageInstruction": [{"text": "10 mg by mouth once daily"}],
        },
    ]

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nLoading James Thornton synthetic data into {FHIR_STORE}...\n")

    print("Patient:")
    put_resource("Patient", PATIENT_ID, patient())

    print("\nEncounter:")
    put_resource("Encounter", ENCOUNTER_ID, encounter())

    print("\nConditions:")
    for r in conditions():
        put_resource("Condition", r["id"], r)

    print("\nObservations:")
    for r in observations():
        put_resource("Observation", r["id"], r)

    print("\nMedicationRequests:")
    for r in medication_requests():
        put_resource("MedicationRequest", r["id"], r)

    print("\n" + "="*60)
    print("Load complete. HC-6 canonical test constants:")
    print(f"  PATIENT_ID   = {PATIENT_ID!r}")
    print(f"  ENCOUNTER_ID = {ENCOUNTER_ID!r}")
    print(f"  FHIR_STORE   = healthcare-dataset/cdss-fhir-store")
    print("\nExpected CDI signals:")
    print("  1. Creatinine rising trend (1.1→1.4→1.8) → AKI N17.9")
    print("  2. Insulin drip ordered → DKA/hyperglycemic crisis E11.10")
    print("  3. Glucose 287 mg/dL → Hyperglycemia R73.09")
    print("  4. WBC 14,200 → SIRS/Infection (no infection coded)")
    print("  5. Albumin 2.7 g/dL → Malnutrition E44")
    print("  6. HbA1c 8.4% + E11.9 coded → Specificity gap (E11.65)")
    print("  7. CKD POA=U → POA clarification query")
    print("="*60)

if __name__ == "__main__":
    main()
