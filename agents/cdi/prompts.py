"""
HC-6 CDI Agent — prompt templates
CDI-3 signal identification and CDI-5 query generation prompts.
"""

from __future__ import annotations
from agents.cdi.tools.load_encounter import EncounterRecord

# ── CDI-3 System Prompt ───────────────────────────────────────────────────────

CDI3_SYSTEM_PROMPT = """You are a Clinical Documentation Integrity (CDI) specialist AI assistant.
Your role is to analyze inpatient encounter records and identify clinical signals that suggest
undocumented diagnoses, documentation gaps, or present-on-admission classification issues.

You have deep knowledge of:
- ICD-10-CM coding guidelines and specificity requirements
- Clinical indicators for common hospital diagnoses
- Present-on-admission (POA) classification rules
- DRG assignment logic and how documentation affects reimbursement

You ALWAYS:
- Cite the exact FHIR resource IDs that constitute the evidence for each signal
- Provide specific ICD-10 codes with decimal qualifiers (N17.9 not N17, E11.65 not E11)
- Apply defined clinical thresholds before flagging a signal
- Return ONLY a valid JSON array with no markdown, no preamble, no explanatory text

You NEVER:
- Flag a condition that is already clearly documented and coded
- Generate signals without citing specific source_resource_ids from the provided data
- Use family-level ICD-10 codes without decimal qualifiers
- Invent resource IDs that were not present in the clinical context"""


# ── CDI-3 User Prompt Builder ─────────────────────────────────────────────────

def build_cdi3_user_prompt(record: EncounterRecord) -> str:
    """
    Build the full CDI-3 prompt with four sections:
    1. CLINICAL CONTEXT — serialized FHIR resources
    2. SIGNAL TAXONOMY — the five signal categories with thresholds
    3. ALREADY CODED DIAGNOSES — suppress false positives
    4. TASK and OUTPUT FORMAT instructions
    """

    # Serialize observations (compact)
    obs_json = _serialize_observations(record.observations)

    # Serialize medication requests (compact)
    med_json = _serialize_medications(record.medication_requests)

    # Serialize conditions (already coded — for suppression context)
    cond_json = _serialize_conditions(record.conditions)

    # Serialize diagnostic reports
    report_json = _serialize_reports(record.diagnostic_reports)

    # Serialize procedures
    proc_json = _serialize_procedures(record.procedures)

    return f"""=== CLINICAL CONTEXT ===

ENCOUNTER ID: {record.encounter_id}
PATIENT ID: {record.patient_id}

OBSERVATIONS AND LAB RESULTS (most recent {len(record.observations)}, sorted newest first):
{obs_json}

MEDICATION ORDERS:
{med_json}

DIAGNOSTIC REPORTS:
{report_json}

PROCEDURES PERFORMED:
{proc_json}

=== ALREADY CODED DIAGNOSES (DO NOT re-signal these unless POA classification is wrong) ===
{cond_json}

=== SIGNAL TAXONOMY ===

You must scan the clinical context above for signals in these five categories.
Apply ALL thresholds strictly before flagging.

CATEGORY 1 — LAB ABNORMALITIES:
- Creatinine RISING TREND: flag AKI (N17.9) ONLY if 3+ readings show upward trajectory with final value >= 1.5x first value
- Serum lactate > 2.0 mmol/L: flag Sepsis/Tissue Hypoperfusion (A41.9)
- WBC > 12,000 /uL OR < 4,000 /uL: flag Infection/SIRS if no infection diagnosis coded
- Procalcitonin > 0.5 ng/mL: flag Bacterial Infection
- Blood glucose > 250 mg/dL in a non-diabetic OR > 300 mg/dL in a known diabetic: flag Hyperglycemia (R73.09)
- Albumin < 3.0 g/dL: flag Malnutrition (E44)
- Sodium < 130 mEq/L: flag Hyponatremia (E87.1)
- INR > 1.5 not on anticoagulation: flag Coagulopathy
- Any troponin elevation: flag Myocardial Injury (I21.9)
- pH < 7.35: flag Acidosis

CATEGORY 2 — MEDICATION ORDERS:
- Insulin drip (continuous IV infusion): flag DKA or Hyperglycemic Crisis (E11.10 or E13.00)
- Vasopressors (norepinephrine, vasopressin, dopamine): flag Septic Shock (A41.9/R57.2)
- Broad-spectrum IV antibiotics (vancomycin + piperacillin-tazobactam together): flag Sepsis (A41.9)
- IV furosemide > 80mg/day: flag Acute Decompensated Heart Failure (I50.9)
- Lactulose: flag Hepatic Encephalopathy (K72.90)
- Naloxone administration: flag Opioid Overdose (T40.2X1A)
- Therapeutic heparin drip: flag DVT or PE (I82.40/I26.99)
- IV pantoprazole stress ulcer prophylaxis: flag Critical Illness

CATEGORY 3 — OBSERVATION PATTERNS:
- Glasgow Coma Scale < 13 on 3+ readings: flag Altered Mental Status (R41.3)
- SpO2 < 92% requiring supplemental O2: flag Hypoxic Respiratory Failure (J96.00)
- HR > 100 persistently (3+ readings): flag SIRS criterion
- Systolic BP < 90 persistently (3+ readings): flag Hypotension/Shock (R03.1)
- Temperature > 38.3C or < 36C: flag SIRS/Infection criterion
- RR > 20 persistently: flag Respiratory Distress (R06.09)

CATEGORY 4 — PROCEDURE INCONSISTENCY:
- Mechanical ventilation present but no Respiratory Failure (J96.xx) coded: flag query
- Hemodialysis or CRRT present but no AKI or CKD coded: flag query
- Arterial line present but no sepsis/shock coded: flag query (cross-check other signals)
- Thoracentesis present but no Pleural Effusion (J91.x) coded: flag query
- Paracentesis present but no Ascites (R18.x) coded: flag query

CATEGORY 5 — PRESENT-ON-ADMISSION AMBIGUITY:
- Any condition coded POA=U (unknown) where lab/vital evidence in first 24h clearly indicates the condition was present: flag for POA=Y query
- Any condition coded POA=Y where first occurrence in labs/vitals is after 48h of admission: flag for POA=N query
- Any condition coded POA=U or POA=W where enough clinical evidence exists to make a determination: flag for POA clarification

CONFIDENCE SCORING GUIDANCE:
- 0.85-1.0 (HIGH): Clear, unambiguous clinical indicator with direct FHIR evidence and no alternative explanation
- 0.70-0.84 (MEDIUM): Probable finding that requires clinical judgment to confirm
- 0.50-0.69 (LOW): Possible finding but ambiguous — include in output, will be routed to internal review only
- Below 0.50: Do not include

SPECIFICITY IMPROVEMENT SIGNALS:
- T2DM coded as E11.9 (unspecified) but HbA1c >= 8.0%: suggest E11.65 (with hyperglycemia) — confidence 0.80
- CKD coded with POA=U but creatinine values present on admission: flag POA clarification

=== TASK ===

Analyze ALL clinical context above. For each signal you identify:
1. Determine which of the five categories it belongs to
2. Check it has NOT already been coded (see ALREADY CODED DIAGNOSES above)
3. Apply the confidence threshold — do not include signals below 0.50
4. Cite the EXACT resource IDs from the clinical context as source_resource_ids
5. Provide a specific ICD-10 code with decimal qualifier

=== OUTPUT FORMAT ===

Return ONLY a JSON array of ClinicalSignal objects. No markdown. No preamble. No explanation.
Each object must have exactly these fields:

[
  {{
    "signal_type": "lab_abnormality" | "medication_order" | "observation_pattern" | "procedure_inconsistency" | "poa_ambiguity",
    "signal_description": "concise description of what was observed",
    "source_resource_ids": ["exact-fhir-id-1", "exact-fhir-id-2"],
    "implied_condition": "human-readable condition name",
    "implied_icd10": "X00.0",
    "confidence": 0.00
  }}
]

If no signals are found, return an empty array: []"""


def _serialize_observations(observations: list[dict]) -> str:
    compact = []
    for o in observations:
        code_text = o.get("code", {}).get("text", "")
        codings = o.get("code", {}).get("coding", [])
        loinc = next((c.get("code") for c in codings if "loinc" in c.get("system", "").lower()), "")
        value_qty = o.get("valueQuantity", {})
        value = f"{value_qty.get('value', '')} {value_qty.get('unit', '')}".strip()
        if not value:
            value = o.get("valueString", o.get("valueCodeableConcept", {}).get("text", "N/A"))
        effective = o.get("effectiveDateTime", o.get("effectivePeriod", {}).get("start", ""))
        compact.append({
            "id": o.get("id"),
            "code": code_text or loinc,
            "loinc": loinc,
            "value": value,
            "effectiveDateTime": effective,
        })
    return _to_json(compact)


def _serialize_medications(medications: list[dict]) -> str:
    compact = []
    for m in medications:
        med_concept = m.get("medicationCodeableConcept", {})
        name = med_concept.get("text", "")
        if not name:
            codings = med_concept.get("coding", [])
            name = codings[0].get("display", "") if codings else ""
        dosage = ""
        dosage_list = m.get("dosageInstruction", [])
        if dosage_list:
            dosage = dosage_list[0].get("text", "")
        compact.append({
            "id": m.get("id"),
            "medication": name,
            "status": m.get("status"),
            "dosage": dosage,
            "authoredOn": m.get("authoredOn", ""),
        })
    return _to_json(compact)


def _serialize_conditions(conditions: list[dict]) -> str:
    compact = []
    for c in conditions:
        code_obj = c.get("code", {})
        icd10 = ""
        display = code_obj.get("text", "")
        for coding in code_obj.get("coding", []):
            if "icd-10" in coding.get("system", "").lower():
                icd10 = coding.get("code", "")
                display = coding.get("display", display)
                break
        compact.append({
            "id": c.get("id"),
            "icd10": icd10,
            "description": display,
            "status": c.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", ""),
            "verification": c.get("verificationStatus", {}).get("coding", [{}])[0].get("code", ""),
        })
    return _to_json(compact)


def _serialize_reports(reports: list[dict]) -> str:
    if not reports:
        return "[]"
    compact = []
    for r in reports:
        compact.append({
            "id": r.get("id"),
            "code": r.get("code", {}).get("text", ""),
            "status": r.get("status", ""),
            "conclusion": r.get("conclusion", ""),
            "issued": r.get("issued", ""),
        })
    return _to_json(compact)


def _serialize_procedures(procedures: list[dict]) -> str:
    if not procedures:
        return "[]"
    compact = []
    for p in procedures:
        code_obj = p.get("code", {})
        name = code_obj.get("text", "")
        codings = code_obj.get("coding", [])
        if not name and codings:
            name = codings[0].get("display", "")
        compact.append({
            "id": p.get("id"),
            "procedure": name,
            "status": p.get("status", ""),
            "performedDateTime": p.get("performedDateTime", ""),
        })
    return _to_json(compact)


def _to_json(obj: object) -> str:
    import json
    return json.dumps(obj, indent=2)


# ── CDI-5 Query Generation Prompt ────────────────────────────────────────────

CDI5_SYSTEM_PROMPT = """You are a Clinical Documentation Integrity (CDI) specialist drafting
physician queries. Your queries are professional, specific, and non-leading.
They follow the standard CDI query format used in hospital CDI programs.
You always provide clinically specific response options that give the physician
clear choices. You return ONLY valid JSON with no markdown or preamble."""


def build_cdi5_user_prompt(
    gap_type: str,
    signal_description: str,
    implied_condition: str,
    implied_icd10: str,
    encounter_id: str,
) -> str:
    return f"""Generate a CDI physician query for the following documentation gap.

ENCOUNTER: {encounter_id}
GAP TYPE: {gap_type}
CLINICAL SIGNAL: {signal_description}
IMPLIED CONDITION: {implied_condition}
SUGGESTED ICD-10: {implied_icd10}

Generate a structured CDI query in this exact JSON format:
{{
  "clinical_indicator": "one sentence describing what was found in the record",
  "clinical_question": "specific yes/no/specify question for the physician",
  "please_clarify": "what specific documentation is needed",
  "response_options": ["option 1", "option 2", "option 3", "clinically undetermined"],
  "query_text": "full formatted query combining all four sections",
  "priority": "ROUTINE or URGENT"
}}

PRIORITY RULES:
- URGENT: signals suggesting sepsis, septic shock, respiratory failure, AKI with rising creatinine, or DKA
- ROUTINE: all other documentation gaps and specificity improvements

The query_text field must combine all sections in this format:
Clinical indicator: [clinical_indicator]
Clinical question: [clinical_question]
Please clarify: [please_clarify]
Response options: [numbered list of response_options]

Return ONLY the JSON object. No markdown. No preamble."""
