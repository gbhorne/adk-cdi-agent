"""
HC-6 CDI Agent — Cloud Run entry point
Handles HTTP requests from Cloud Scheduler (sweep mode) and
direct invocation (single encounter mode).
Also serves as the ADK web UI entry point.
"""

from __future__ import annotations
import json
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

from agents.cdi.agent import run_cdi_pipeline, run_nightly_sweep
from agents.cdi.adk_agent import cdi_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="HC-6 CDI Agent",
    description="Clinical Documentation Integrity Agent — Healthcare Agentic AI Portfolio",
    version="1.0.0",
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "hc6-cdi-agent"}


@app.post("/run")
async def run_single(request: Request):
    """
    Run CDI pipeline for a single encounter.
    Body: {"encounter_id": "...", "patient_id": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    encounter_id = body.get("encounter_id")
    if not encounter_id:
        raise HTTPException(status_code=400, detail="encounter_id is required")

    logger.info(f"Single encounter run requested: {encounter_id}")
    result = run_cdi_pipeline(encounter_id)
    return JSONResponse(content=result.model_dump())


@app.post("/sweep")
async def sweep(request: Request):
    """
    Run CDI pipeline for all active inpatient encounters.
    Triggered by Cloud Scheduler via Pub/Sub push or direct HTTP.
    """
    # Pub/Sub push messages arrive with a base64-encoded body wrapper
    try:
        body = await request.json()
        message = body.get("message", {})
        if message:
            import base64
            data_bytes = base64.b64decode(message.get("data", "e30="))
            payload = json.loads(data_bytes)
            mode = payload.get("mode", "sweep")
        else:
            mode = body.get("mode", "sweep")
    except Exception:
        mode = "sweep"

    logger.info(f"Sweep triggered — mode={mode}")
    results = run_nightly_sweep()
    return JSONResponse(content={
        "encounters_processed": len(results),
        "total_tasks_written":  sum(r.tasks_written for r in results),
        "total_errors":         sum(len(r.errors) for r in results),
    })


# ADK web UI entry point
# `adk web` discovers the agent via the `agent` module attribute
agent = cdi_agent

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("agent:app", host="0.0.0.0", port=port, reload=False)
