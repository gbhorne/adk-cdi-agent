"""
HC-6 CDI Agent — CDI-5: generate_cdi_queries
For each gap above the confidence threshold, Gemini generates a structured
CDI physician query in standard CDI format.
"""

from __future__ import annotations
import json
import logging

from google import genai
from google.genai import types

from shared.config import GEMINI_MODEL
from shared.models import CDIQuery, DiagnosisGap, GapAnalysis
from agents.cdi.prompts import CDI5_SYSTEM_PROMPT, build_cdi5_user_prompt

logger = logging.getLogger(__name__)


def _get_client() -> genai.Client:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=api_key)


def generate_cdi_queries(gap_analysis: GapAnalysis) -> list[CDIQuery]:
    """
    CDI-5: Generate a structured CDI physician query for each gap
    where query_warranted=True (confidence >= CONFIDENCE_THRESHOLD).
    """
    actionable_gaps = [g for g in gap_analysis.gaps if g.query_warranted]

    if not actionable_gaps:
        logger.info("CDI-5: No gaps above confidence threshold — skipping query generation")
        return []

    client = _get_client()
    queries: list[CDIQuery] = []

    for gap in actionable_gaps:
        query = _generate_single_query(client, gap, gap_analysis.encounter_id)
        if query:
            queries.append(query)

    logger.info(
        f"CDI-5: Generated {len(queries)}/{len(actionable_gaps)} queries "
        f"for encounter {gap_analysis.encounter_id}"
    )
    return queries


def _generate_single_query(
    client: genai.Client,
    gap: DiagnosisGap,
    encounter_id: str,
) -> CDIQuery | None:
    """Generate one CDI query via Gemini. Returns None on parse failure."""
    prompt = build_cdi5_user_prompt(
        gap_type=gap.gap_type.value,
        signal_description=gap.signal.signal_description,
        implied_condition=gap.signal.implied_condition,
        implied_icd10=gap.signal.implied_icd10,
        encounter_id=encounter_id,
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=CDI5_SYSTEM_PROMPT,
                temperature=0.2,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()
    except Exception as e:
        logger.error(
            f"CDI-5 Gemini call failed for gap '{gap.signal.implied_condition}': {e}"
        )
        return None

    return _parse_query_response(raw, gap)


def _parse_query_response(raw_json: str, gap: DiagnosisGap) -> CDIQuery | None:
    """Parse and validate Gemini CDI query JSON output."""
    raw_json = raw_json.strip()
    if raw_json.startswith("```"):
        lines = raw_json.split("\n")
        raw_json = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        )

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.error(f"CDI-5 JSON parse error: {e} | raw={raw_json[:300]}")
        return None

    required_fields = [
        "clinical_indicator", "clinical_question",
        "please_clarify", "response_options", "query_text", "priority"
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        logger.warning(f"CDI-5 query missing fields {missing} for gap '{gap.signal.implied_condition}'")
        return None

    priority = data.get("priority", "ROUTINE").upper()
    if priority not in ("ROUTINE", "URGENT"):
        priority = "ROUTINE"

    response_options = data.get("response_options", [])
    if isinstance(response_options, str):
        response_options = [response_options]

    undetermined_present = any(
        "undetermined" in opt.lower() or "unable to determine" in opt.lower()
        for opt in response_options
    )
    if not undetermined_present:
        response_options.append("Clinically undetermined")

    return CDIQuery(
        gap=gap,
        clinical_indicator=data["clinical_indicator"],
        clinical_question=data["clinical_question"],
        please_clarify=data["please_clarify"],
        response_options=response_options,
        query_text=data["query_text"],
        priority=priority,
    )
