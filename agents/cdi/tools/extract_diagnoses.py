"""
HC-6 CDI Agent — CDI-2: extract_coded_diagnoses
Parses Condition resources into a typed CodedDiagnosisIndex.
"""

from __future__ import annotations
from shared.models import CodedDiagnosis, CodedDiagnosisIndex, DiagnosisRole, POAFlag
from agents.cdi.tools.load_encounter import EncounterRecord

# LOINC / NUBC POA indicator system
_POA_SYSTEM = "https://www.nubc.org/CodeSystem/POAIndicator"

# Condition category codes that map to diagnosis roles
_ROLE_MAP = {
    "encounter-diagnosis": DiagnosisRole.PRINCIPAL,
    "problem-list-item":   DiagnosisRole.COMORBIDITY,
    "health-concern":      DiagnosisRole.COMORBIDITY,
}


def extract_coded_diagnoses(record: EncounterRecord) -> CodedDiagnosisIndex:
    """
    CDI-2: Parse all Condition resources into a structured CodedDiagnosisIndex.
    Extracts ICD-10 code, description, diagnosis role, POA flag, and confirmation status.
    """
    diagnoses: list[CodedDiagnosis] = []

    for condition in record.conditions:
        # ICD-10 code and display text
        icd10_code, description = _extract_icd10(condition)
        if not icd10_code:
            continue  # skip conditions without ICD-10 coding

        # Diagnosis role from category
        role = _extract_role(condition)

        # POA flag from extension
        poa_flag = _extract_poa(condition)

        # Confirmation / verification status
        confirmation = _extract_confirmation(condition)

        diagnoses.append(CodedDiagnosis(
            condition_id=condition.get("id", "unknown"),
            icd10_code=icd10_code,
            description=description,
            role=role,
            poa_flag=poa_flag,
            confirmation_status=confirmation,
        ))

    return CodedDiagnosisIndex(
        encounter_id=record.encounter_id,
        diagnoses=diagnoses,
    )


def _extract_icd10(condition: dict) -> tuple[str, str]:
    """Extract the ICD-10-CM code and display text from a Condition resource."""
    code_obj = condition.get("code", {})
    display = code_obj.get("text", "")

    for coding in code_obj.get("coding", []):
        system = coding.get("system", "")
        if "icd-10" in system.lower() or "icd10" in system.lower():
            code = coding.get("code", "").strip()
            disp = coding.get("display", display).strip()
            return code, disp or display

    # Fallback: return first coding regardless of system
    codings = code_obj.get("coding", [])
    if codings:
        return codings[0].get("code", ""), codings[0].get("display", display)

    return "", display


def _extract_role(condition: dict) -> DiagnosisRole:
    """Map FHIR condition category to DiagnosisRole enum."""
    for cat in condition.get("category", []):
        for coding in cat.get("coding", []):
            code = coding.get("code", "")
            if code in _ROLE_MAP:
                return _ROLE_MAP[code]
    return DiagnosisRole.SECONDARY


def _extract_poa(condition: dict) -> POAFlag:
    """
    Extract Present-on-Admission flag from FHIR extension.
    Checks both US Core and QICore extension URLs.
    """
    poa_extension_urls = [
        "http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-present-on-admission",
        "https://hl7.org/fhir/us/core/StructureDefinition/us-core-present-on-admission",
    ]

    for ext in condition.get("extension", []):
        if ext.get("url") in poa_extension_urls:
            value_coding = ext.get("valueCoding", {})
            code = value_coding.get("code", "").upper()
            try:
                return POAFlag(code)
            except ValueError:
                pass

    return POAFlag.UNKNOWN


def _extract_confirmation(condition: dict) -> str:
    """Extract verification status: confirmed, provisional, differential, refuted."""
    ver_status = condition.get("verificationStatus", {})
    for coding in ver_status.get("coding", []):
        code = coding.get("code", "")
        if code in ("confirmed", "provisional", "differential", "refuted", "entered-in-error"):
            return code
    return "confirmed"


def format_diagnosis_index_for_log(index: CodedDiagnosisIndex) -> str:
    """Human-readable summary for logging."""
    lines = [f"Coded diagnoses for encounter {index.encounter_id}:"]
    for d in index.diagnoses:
        lines.append(
            f"  {d.icd10_code:12s} {d.description:50s} POA={d.poa_flag.value}  "
            f"role={d.role.value}  status={d.confirmation_status}"
        )
    return "\n".join(lines)
