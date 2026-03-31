# HC-6 CDI Agent: Technical Q&A

This document covers common technical questions about the HC-6 Clinical Documentation Integrity Agent build.

---

## Architecture

**Q: Why does the pipeline have six discrete steps instead of one large Gemini call?**

Each step has a different failure mode and a different data contract. CDI-1 and CDI-2 are deterministic FHIR operations with no LLM involvement. CDI-3 is the most prompt-engineering-intensive step. CDI-4 is pure Python logic with no LLM call. CDI-5 calls Gemini again with a much smaller, focused prompt. CDI-6 is pure I/O. Separating them means you can test each step independently, retry individual steps on failure, and swap implementations without touching the others. A single monolithic Gemini call would also exceed practical token limits for complex encounters.

**Q: Why is CDI-4 pure Python rather than another Gemini call?**

The gap classification logic is deterministic and rule-based: exact ICD-10 match resolves the signal, family-level match with a more specific implied code creates a specificity gap, no match creates an undocumented condition gap. These are finite, well-defined cases. Sending them to Gemini would introduce non-determinism, latency, and cost for zero benefit. LLM calls are reserved for the two steps that genuinely require language understanding: CDI-3 (scanning clinical text for signals) and CDI-5 (generating natural-language physician queries).

**Q: Why does CDI-3 use temperature=0.1 and CDI-5 uses temperature=0.2?**

CDI-3 is a signal detection task where consistency matters most. The same clinical context should produce the same signals on repeated runs. Low temperature keeps Gemini close to the most probable output. CDI-5 generates physician-facing text where some variation in wording is acceptable and desirable, so temperature is slightly higher. Neither task benefits from the creative variation that higher temperatures enable.

**Q: What is the purpose of the valid_resource_ids set built in CDI-1?**

It is the hallucination firewall for CDI-3. Before calling Gemini, the loader captures the IDs of every FHIR resource loaded from the store. After Gemini returns signals, every source_resource_ids value is validated against this set. Any signal citing a resource that does not exist in the actual FHIR bundle is dropped before it can reach CDI-4. This prevents a common failure mode where Gemini generates plausible-sounding but fabricated resource IDs as evidence for a signal.

**Q: Why does the pipeline use search-based FHIR loading instead of the $everything operation?**

Google Cloud Healthcare API supports $everything only on Patient resources, not on Encounter resources. Attempting $everything on an Encounter returns a 404. The workaround is to fetch the Encounter resource directly, then run individual search queries for each resource type using the ?encounter=Encounter/{id} filter parameter. This actually provides more control because you can cap observation counts, handle pagination per resource type, and skip resource types that do not support encounter search without failing the entire load.

---

## Clinical Logic

**Q: Why does the creatinine AKI signal require three readings instead of one elevated value?**

The KDIGO (Kidney Disease: Improving Global Outcomes) definition of AKI requires a serum creatinine rise of 1.5x baseline within 7 days, or an absolute increase of 0.3 mg/dL within 48 hours, or oliguria. A single elevated creatinine reading in a patient with CKD may simply reflect their baseline. Flagging AKI on a single value would generate a high volume of false-positive physician queries, which destroys CDI specialist trust in the system. The three-reading requirement with a rising trend reduces false positives while still catching the clinically significant pattern.

**Q: What is the difference between the three gap types?**

UNDOCUMENTED_CONDITION means the implied diagnosis has no corresponding Condition resource in the record at all. The signal implies N17.9 (AKI) and there is no coded AKI diagnosis. SPECIFICITY_IMPROVEMENT means the condition is coded but at a less specific level than the signal implies. The signal implies E11.65 (T2DM with hyperglycemia) but only E11.9 (T2DM unspecified) is coded. The diagnosis exists but needs a more precise code. POA_CLARIFICATION means the condition is coded but its present-on-admission flag is U (unknown) or W (clinically undetermined) and the record contains enough evidence to make a determination.

**Q: Why does the confidence gate sit at 0.7 specifically?**

In CDI programs, the most damaging outcome is not missing a gap but sending a physician a weak or spurious query. Physicians who receive low-quality CDI queries stop responding to them entirely, which breaks the whole workflow. The 0.7 threshold was chosen to pass signals where Gemini has identified a clear clinical indicator with direct FHIR evidence (HIGH confidence at 0.85 or above) or probable findings that require clinical judgment (MEDIUM at 0.7 to 0.84), while routing ambiguous signals (below 0.7) to internal CDI specialist review. The threshold is configurable via the CONFIDENCE_THRESHOLD environment variable.

**Q: Why is "Clinically undetermined" always added to physician query response options?**

CDI coding guidelines and CMS rules require that physician queries be non-leading. A query that does not offer "clinically undetermined" as a response option effectively forces the physician to choose a diagnosis, which is considered a leading query and can create compliance issues. The generate_queries.py parser enforces this by checking the response_options list and appending "Clinically undetermined" if Gemini omits it.

**Q: How does the ICD-10 specificity validator work and why does it not reject all three-character codes?**

ICD-10-CM includes some legitimately billable codes at three characters. E44 (protein-calorie malnutrition, unspecified) and I10 (essential hypertension) are valid specific codes with no required decimal qualifier. The validator maintains a whitelist of known non-billable family codes that always require decimal qualifiers: E11, E13, N17, N18, A41, I50, I26, I82, J96, K72, and T40. If Gemini returns one of these, the signal is rejected. Codes not on the list pass through regardless of length. This approach avoids rejecting legitimate three-character codes while catching the most common family codes that Gemini tends to return without specificity.

---

## GCP Infrastructure

**Q: Why does CDI-6 write to both BigQuery and Firestore?**

They serve different purposes. BigQuery is the audit log for every CDI query generated above the confidence threshold. It is designed for downstream analytics: how many queries per encounter type, what is the acceptance rate by query type, which signal categories generate the most gaps. Firestore stores low-confidence signals that do not reach physicians. These are reviewed by CDI specialists who can manually promote a signal to a query if they judge the clinical context warrants it. BigQuery is append-only audit; Firestore is operational state.

**Q: Why does the Pub/Sub message go to cdi-review-ready instead of being sent directly to a notification service?**

Decoupling the CDI agent from downstream notification systems keeps the pipeline flexible. A CDI coordinator's system, a paging service, an EHR integration, and a CDI dashboard can all subscribe to cdi-review-ready independently. The agent does not need to know who is listening or how they consume the notification. This also means the agent can be tested without triggering production notifications by simply not having subscribers on the test topic.

**Q: How does the Cloud Scheduler nightly sweep work?**

The Cloud Scheduler job fires at 02:00 UTC and publishes a message to the cdi-trigger Pub/Sub topic with the body {"mode": "sweep", "triggered_by": "scheduler"}. The Cloud Run service subscribes via the cdi-agent-sub pull subscription. On receiving a sweep message, the run_nightly_sweep function calls the FHIR store with Encounter?status=in-progress to get all active inpatient encounters, then runs run_cdi_pipeline for each encounter ID. Results are aggregated and errors are logged per encounter without stopping the sweep for other encounters.

**Q: Why is the BigQuery write synchronous in the test environment?**

The google-cloud-bigquery client's insert_rows_json method is a blocking streaming insert. In production this should be wrapped in asyncio's loop.run_in_executor to avoid blocking the Cloud Run request handler while waiting for the BigQuery API response. In the test environment, synchronous behavior is acceptable because the pipeline runs as a single blocking call, not as an async request handler. The architecture is designed so the production Cloud Run deployment can add the executor wrapper without changing any other code.

---

## Google ADK

**Q: Why does the ADK agent use a single tool (tool_write_tasks) instead of six separate tools for each pipeline step?**

A design choice for the portfolio demonstration. The six separate tool functions exist in earlier iterations of the code, but the ADK Web UI trace view is cleaner with one tool call that shows the complete pipeline result. For a production deployment where a CDI specialist might want to inspect intermediate results or approve signals before queries are generated, six separate tools would be the better design. The current single-tool approach optimizes for the demo scenario.

**Q: Why does the ADK agent definition use root_agent as the variable name?**

Google ADK's agent loader specifically looks for a variable named root_agent in the agent.py module when running adk web. It searches cdi.agent.root_agent first, then cdi.root_agent. Using any other variable name (such as cdi_agent or agent) causes a ValueError at startup. This is an ADK convention that differs from how you might name the variable in your own code.

**Q: Why is sys.path.insert needed at the top of agents/cdi/agent.py?**

When adk web launches, it sets the working directory to the agents/ folder and imports the module as cdi.agent. The shared/ package lives at the project root, one level above agents/. Without explicitly adding the project root to sys.path, Python cannot find the shared module. The path injection at the top of agent.py resolves this by adding os.path.join(os.path.dirname(__file__), "..", "..") to sys.path before any imports. The from __future__ import annotations declaration must come first as a Python requirement, with the path injection immediately after.

**Q: Why does the deprecated vertexai SDK not work for this project?**

The google-cloud-aiplatform package's vertexai.generative_models module was deprecated as of June 2025 and shows a UserWarning. More critically, the specific model strings it uses (projects/{id}/locations/{region}/publishers/google/models/{model}) require that the model be explicitly enabled in your GCP project via the Vertex AI API console. The google-genai SDK uses the Google AI Studio API endpoint directly with an API key, bypassing the GCP project-level model enablement requirement. For projects using Google AI Studio keys, the google-genai SDK is the correct choice.

---

## Testing

**Q: What does the integration test suite verify that the unit tests do not?**

Unit tests verify logic in isolation using synthetic Python objects. They test that the ICD-10 validator rejects family codes, that the confidence gate correctly filters signals, that POA ambiguity signals are classified correctly. Integration tests verify the full pipeline against live GCP infrastructure. They confirm that CDI-1 actually loads all six resource types from the real FHIR store, that CDI-3 correctly identifies the AKI signal from the actual creatinine readings in the loaded data, that CDI-6 writes Task resources that can be retrieved from the FHIR store with the correct status and structure, and that BigQuery receives the audit rows.

**Q: Why does test_integration.py run the pipeline multiple times?**

Different test classes call different pipeline stages independently. TestCDI1LoadEncounter calls load_encounter_record directly. TestCDI3IdentifySignals calls CDI-1 and CDI-3. TestFullPipeline runs the entire six-step pipeline. This creates multiple runs against the live FHIR store and generates duplicate Task resources in the store and BigQuery. This is expected behavior for an integration test suite against a development FHIR store. A production implementation would use a dedicated test FHIR store with cleanup logic between runs.

**Q: What is the significance of the test_no_family_codes_in_signals integration test?**

It validates the ICD-10 specificity requirement end-to-end against a live Gemini response. The unit tests verify that the Pydantic validator rejects known non-billable family codes if they appear in a signal object. The integration test verifies that after running the full CDI-3 pipeline against real FHIR data with real Gemini, none of the returned signals contain non-billable family codes. If Gemini starts returning family codes despite the prompt instructions, this test will fail and surface the regression.
