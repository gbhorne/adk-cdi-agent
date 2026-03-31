"""
HC-6 CDI Agent — Pydantic models
All typed data contracts for the six-step CDI pipeline.
"""

from __future__ import annotations
from pydantic import BaseModel, field_validator
from typing import Optional, List
from enum import Enum


# ── Enumerations ──────────────────────────────────────────────────────────────

class SignalType(str, Enum):
    LAB_ABNORMALITY        = "lab_abnormality"
    MEDICATION_ORDER       = "medication_order"
    OBSERVATION_PATTERN    = "observation_pattern"
    PROCEDURE_INCONSISTENCY = "procedure_inconsistency"
    POA_AMBIGUITY          = "poa_ambiguity"


class GapType(str, Enum):
    UNDOCUMENTED_CONDITION  = "undocumented_condition"
    POA_CLARIFICATION       = "poa_clarification"
    SPECIFICITY_IMPROVEMENT = "specificity_improvement"


class POAFlag(str, Enum):
    YES                  = "Y"   # present on admission
    NO                   = "N"   # not present on admission
    UNKNOWN              = "U"   # unknown
    CLINICALLY_UNDETERMINED = "W"


class DiagnosisRole(str, Enum):
    PRINCIPAL    = "principal"
    SECONDARY    = "secondary"
    COMORBIDITY  = "comorbidity"
    COMPLICATION = "complication"


# ── CDI-2 output ──────────────────────────────────────────────────────────────

class CodedDiagnosis(BaseModel):
    condition_id:          str
    icd10_code:            str
    description:           str
    role:                  DiagnosisRole = DiagnosisRole.SECONDARY
    poa_flag:              POAFlag = POAFlag.UNKNOWN
    confirmation_status:   str = "confirmed"   # confirmed | provisional | differential | refuted


class CodedDiagnosisIndex(BaseModel):
    encounter_id:  str
    diagnoses:     List[CodedDiagnosis]

    def icd10_codes(self) -> set[str]:
        return {d.icd10_code for d in self.diagnoses}

    def find_by_icd10(self, code: str) -> Optional[CodedDiagnosis]:
        """Exact match first, then 3-character family prefix match."""
        for d in self.diagnoses:
            if d.icd10_code == code:
                return d
        for d in self.diagnoses:
            if d.icd10_code.startswith(code[:3]):
                return d
        return None


# ── CDI-3 output ──────────────────────────────────────────────────────────────

class ClinicalSignal(BaseModel):
    signal_type:         SignalType
    signal_description:  str
    source_resource_ids: List[str]
    implied_condition:   str
    implied_icd10:       str
    confidence:          float

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return round(v, 4)

    @field_validator("implied_icd10")
    @classmethod
    def validate_icd10_specificity(cls, v: str) -> str:
        """
        Reject known non-billable family codes that require further specificity.
        Note: some valid ICD-10-CM codes are legitimately 3 chars (E44, I10, Z23).
        Only reject codes from families that always require a decimal qualifier.
        """
        v = v.strip()
        non_billable_families = {
            "E11", "E13", "N17", "N18", "A41", "I50", "I26",
            "I82", "J96", "K72", "T40",
        }
        if v in non_billable_families:
            raise ValueError(
                f"ICD-10 code '{v}' is a non-billable family code. "
                "Provide a specific code with decimal qualifier (e.g. N17.9 not N17)."
            )
        return v


# ── CDI-4 output ──────────────────────────────────────────────────────────────

class DiagnosisGap(BaseModel):
    gap_type:         GapType
    signal:           ClinicalSignal
    query_warranted:  bool   # False if confidence < CONFIDENCE_THRESHOLD


class GapAnalysis(BaseModel):
    encounter_id:          str
    resolved_signals:      List[ClinicalSignal]
    gaps:                  List[DiagnosisGap]
    total_signals:         int
    gaps_above_threshold:  int
    gaps_below_threshold:  int


# ── CDI-5 output ──────────────────────────────────────────────────────────────

class CDIQuery(BaseModel):
    gap:                DiagnosisGap
    clinical_indicator: str
    clinical_question:  str
    please_clarify:     str
    response_options:   List[str]
    query_text:         str   # full formatted query (all 4 sections)
    priority:           str   # ROUTINE | URGENT


# ── CDI-6 output ──────────────────────────────────────────────────────────────

class CDIPipelineResult(BaseModel):
    encounter_id:    str
    patient_id:      str
    signals_found:   int
    gaps_identified: int
    tasks_written:   int
    task_ids:        List[str]
    pubsub_message_id: Optional[str] = None
    errors:          List[str] = []
