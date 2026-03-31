"""
HC-6 CDI Agent — shared configuration
All environment-driven constants for the pipeline.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── GCP ───────────────────────────────────────────────────────────────────────
PROJECT_ID   = os.getenv("GCP_PROJECT_ID", "healthcare-sa-2026")
LOCATION     = os.getenv("GCP_LOCATION", "us-central1")

# ── FHIR ──────────────────────────────────────────────────────────────────────
FHIR_DATASET    = os.getenv("FHIR_DATASET", "healthcare-dataset")
FHIR_STORE      = os.getenv("FHIR_STORE", "cdss-fhir-store")
FHIR_BASE_URL   = (
    f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}"
    f"/locations/{LOCATION}/datasets/{FHIR_DATASET}/fhirStores/{FHIR_STORE}/fhir"
)

# ── Vertex AI / Gemini ────────────────────────────────────────────────────────
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-04-17")
VERTEXAI_USE    = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"

# ── Pub/Sub ───────────────────────────────────────────────────────────────────
PUBSUB_TRIGGER_TOPIC  = os.getenv("PUBSUB_TRIGGER_TOPIC", "cdi-trigger")
PUBSUB_NOTIFY_TOPIC   = os.getenv("PUBSUB_NOTIFY_TOPIC", "cdi-review-ready")
PUBSUB_SUBSCRIPTION   = os.getenv("PUBSUB_SUBSCRIPTION", "cdi-agent-sub")

# ── BigQuery ──────────────────────────────────────────────────────────────────
BQ_DATASET      = os.getenv("BQ_DATASET", "cdi_analytics")
BQ_TABLE        = os.getenv("BQ_TABLE", "cdi_queries")
BQ_TABLE_FQN    = f"{PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

# ── Firestore ─────────────────────────────────────────────────────────────────
FS_PROCESSING_COLLECTION = "cdi_processing_state"
FS_HISTORY_COLLECTION    = "cdi_query_history"

# ── CDI pipeline constants ────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD     = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))
TASK_RESPONSE_WINDOW_HRS = int(os.getenv("TASK_RESPONSE_WINDOW_HRS", "48"))
MAX_OBSERVATIONS         = int(os.getenv("MAX_OBSERVATIONS", "20"))   # cap for CDI-3 context

# ── HC-6 test constants ───────────────────────────────────────────────────────
TEST_PATIENT_ID   = "hc6-patient-thornton-001"
TEST_ENCOUNTER_ID = "hc6-encounter-thornton-001"
