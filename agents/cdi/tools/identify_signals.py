"""
HC-6 CDI Agent — CDI-3: identify_clinical_signals
Gemini scans all observations, labs, medications, and procedures for
clinical signals that imply undocumented or mis-classified diagnoses.
"""

from __future__ import annotations
import json
import logging

from google import genai
from google.genai import types

from shared.config import GEMINI_MODEL
from shared.models import ClinicalSignal, SignalType
from agents.cdi.tools.load_encounter import EncounterRecord
from agents.cdi.prompts import CDI3_SYSTEM_PROMPT, build_cdi3_user_prompt

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=api_key)


def identify_clinical_signals(record: EncounterRecord) -> list[ClinicalSignal]:
    """
    CDI-3: Use Gemini to scan the encounter record for clinical signals
    across all five signal categories. Returns validated ClinicalSignal list.
    """
    client = _get_client()
    user_prompt = build_cdi3_user_prompt(record)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=CDI3_SYSTEM_PROMPT,
            temperature=0.1,
            max_output_tokens=8192,
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text.strip()
    signals = _parse_and_validate_signals(raw_text, record.valid_resource_ids)
    logger.info(f"CDI-3: {len(signals)} validated signals for encounter {record.encounter_id}")
    return signals


def _parse_and_validate_signals(
    raw_json: str,
    valid_resource_ids: set[str],
) -> list[ClinicalSignal]:
    """
    Parse Gemini JSON output into ClinicalSignal objects.
    Applies two layers of validation:
      1. Pydantic schema validation (confidence range, ICD-10 specificity)
      2. Resource ID validation — reject any signal citing a non-existent FHIR ID
    """
    raw_json = raw_json.strip()
    if raw_json.startswith("```"):
        lines = raw_json.split("\n")
        raw_json = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )

    try:
        raw_list = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.error(f"CDI-3 JSON parse error: {e}\nRaw output:\n{raw_json[:500]}")
        return []

    if not isinstance(raw_list, list):
        logger.error(f"CDI-3 expected JSON array, got {type(raw_list)}")
        return []

    validated: list[ClinicalSignal] = []
    for i, item in enumerate(raw_list):
        try:
            signal = ClinicalSignal(**item)
        except Exception as e:
            logger.warning(f"CDI-3 signal[{i}] failed Pydantic validation: {e} | raw={item}")
            continue

        # Resource ID hallucination check
        invalid_ids = [
            rid for rid in signal.source_resource_ids
            if rid not in valid_resource_ids
        ]
        if invalid_ids:
            logger.warning(
                f"CDI-3 signal[{i}] '{signal.implied_condition}' cited non-existent "
                f"resource IDs {invalid_ids} — signal dropped"
            )
            continue

        if not signal.source_resource_ids:
            logger.warning(
                f"CDI-3 signal[{i}] '{signal.implied_condition}' has no source_resource_ids — dropped"
            )
            continue

        validated.append(signal)

    return validated
