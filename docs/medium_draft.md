# I Built an AI Agent That Catches What Doctors Miss — Clinical Documentation Integrity on Google ADK

*Healthcare Agentic AI Portfolio · Project 6 of 9*

---

Every year, hospitals lose billions of dollars in reimbursement not because of bad medicine, but because of incomplete paperwork.

A patient comes in with a rising creatinine, gets put on an insulin drip, and leaves with a DRG that doesn't reflect how sick they actually were. Not because the physician didn't know — they knew. They just didn't document it in a way the billing system could capture.

That gap between what happened clinically and what got coded is what Clinical Documentation Integrity (CDI) specialists exist to close. They read charts, find signals, and send physician queries: "We see creatinine trending up. Can you confirm whether this patient developed Acute Kidney Injury?"

It's important, skilled work. It's also manual, time-consuming, and doesn't scale.

For HC-6, the sixth project in my Healthcare Agentic AI portfolio, I built an agent that does this automatically.

---

## What CDI Actually Is (And Why It Matters)

Before getting into the build, it's worth understanding what's at stake.

Every inpatient hospital stay gets assigned a **DRG** — a Diagnosis Related Group. Medicare and most insurers pay hospitals a fixed amount per DRG, not per individual service. A patient with Type 2 diabetes coded as E11.9 (unspecified) reimburses differently than E11.65 (with hyperglycemia). A patient with undocumented Acute Kidney Injury may cost the hospital $5,000–$15,000 in unrealized reimbursement.

Beyond money, documentation quality affects:
- **Severity of illness scoring** — used for quality metrics and risk adjustment
- **Present-on-admission (POA) classification** — if a condition develops during the stay, the hospital may face CMS penalties for hospital-acquired conditions
- **ICD-10 coding accuracy** — the foundation of every downstream analytics and billing workflow

CDI specialists bridge the gap between clinical reality and coded documentation. The agent I built automates their signal detection and query generation work.

---

## The Six-Step Pipeline

The CDI Agent runs a deterministic six-step pipeline for each inpatient encounter.

### CDI-1: Load the Encounter

The first step pulls the complete encounter record from **Google Cloud Healthcare API** using FHIR R4. Because the Healthcare API doesn't support `$everything` on Encounter resources (it only works on Patient), I built a search-based approach that fetches each resource type individually — Condition, Observation, MedicationRequest, DiagnosticReport, Procedure — and assembles a synthetic Bundle.

One important detail: the loader captures the full set of valid FHIR resource IDs at this step. This set becomes the ground truth for hallucination detection in CDI-3.

### CDI-2: Index the Coded Diagnoses

Before looking for gaps, the agent needs to know what's already documented. CDI-2 parses all Condition resources into a typed `CodedDiagnosisIndex` — extracting ICD-10 codes, diagnosis roles (principal vs. secondary vs. comorbidity), present-on-admission flags, and confirmation status.

The index supports two lookup modes: exact match (E11.9 → E11.9) and family prefix match (E11.65 → E11.9 family). This matters in CDI-4, where the agent needs to determine whether a signal is truly undocumented or just less specific than ideal.

### CDI-3: Identify Clinical Signals

This is where Gemini earns its place.

The prompt is structured in four sections: clinical context (serialized FHIR resources), the signal taxonomy (five categories with specific clinical thresholds), the already-coded diagnoses (for suppression), and the task with output format instructions.

The five signal categories cover:

**Lab Abnormalities** — creatinine rising trend (3+ readings, final ≥ 1.5x baseline → AKI), albumin < 3.0 g/dL → malnutrition, WBC > 12,000 → SIRS, troponin elevation → myocardial injury.

**Medication Orders** — insulin drip → DKA or hyperglycemic crisis, vasopressors → septic shock, broad-spectrum IV antibiotics → sepsis, IV furosemide > 80mg/day → acute decompensated heart failure.

**Observation Patterns** — persistent SpO2 < 92% → hypoxic respiratory failure, persistent hypotension → shock, GCS < 13 on multiple readings → encephalopathy.

**Procedure Inconsistency** — mechanical ventilation without respiratory failure coded, hemodialysis without AKI or CKD documented.

**POA Ambiguity** — conditions coded POA=U (unknown) where admission labs support a determination.

Every signal Gemini identifies must cite the exact FHIR resource IDs that constitute the evidence. After generation, I validate every `source_resource_ids` entry against the set built in CDI-1. Any signal citing a resource that doesn't exist gets dropped. This is the hallucination firewall.

### CDI-4: Match Signals to Diagnoses

CDI-4 cross-references each signal against the `CodedDiagnosisIndex`. The classification logic handles three cases:

- **Exact match** → resolved, no query needed
- **Family-level match but signal implies more specific code** → specificity improvement gap (e.g., E11.65 implied but E11.9 coded)
- **No match** → undocumented condition gap
- **POA signal on a condition with POA=U or POA=W** → POA clarification gap

The **confidence gate** sits at 0.7. Signals below this threshold go to Firestore (`cdi_query_history`) for internal CDI specialist review. They never reach the physician. This is a deliberate design choice — in CDI programs, sending a physician a weak query destroys trust in the system faster than missing a gap.

### CDI-5: Generate CDI Queries

For each gap above the confidence threshold, Gemini generates a structured physician query in the format CDI specialists actually use:

```
Clinical indicator: The patient's creatinine levels rose from 1.1 to 1.4 to 1.8 mg/dL.
Clinical question: Given the rising trend, is the patient experiencing Acute Kidney Injury?
Please clarify: Please document the presence or absence of AKI in the medical record.
Response options:
  1. Acute kidney injury, unspecified (N17.9)
  2. No acute kidney injury
  3. Other (please specify)
  4. Clinically undetermined
```

"Clinically undetermined" is always an option — it's required by CDI coding guidelines and I enforce it in the parser.

### CDI-6: Write Tasks and Notify

The output step writes a **FHIR Task resource** for each query, with a 48-hour response window, ROUTINE or URGENT priority (AKI, sepsis, DKA queries are urgent), and a reference back to the encounter. It also streams an audit row to **BigQuery** (`cdi_analytics.cdi_queries`) and publishes a summary to **Pub/Sub** (`cdi-review-ready`).

---

## Running It on James Thornton

My synthetic test patient, James Thornton, was designed with specific documentation gaps to validate all five signal categories.

His record: Type 2 DM (E11.9), Hypertension (I10), CKD Stage 3 (N18.3 — coded POA=U). Observations: creatinine 1.1→1.4→1.8 mg/dL, HbA1c 8.4%, glucose 287 mg/dL, WBC 14,200, albumin 2.7 g/dL. Medications: insulin drip, metformin, lisinopril.

The agent identified six signals and generated six physician queries:

| Signal | Gap Type | ICD-10 | Confidence | Priority |
|--------|---------|--------|-----------|---------|
| Creatinine trend | Undocumented condition | N17.9 | 0.90 | URGENT |
| Insulin drip | Specificity improvement | E11.10 | 0.90 | URGENT |
| Albumin 2.7 g/dL | Undocumented condition | E44.0 | 0.90 | ROUTINE |
| WBC 14,200 | Undocumented condition | A41.9 | 0.75 | URGENT |
| CKD POA=U | POA clarification | N18.3 | 0.90 | ROUTINE |
| HbA1c 8.4% + E11.9 | POA clarification | E11.65 | 0.80 | ROUTINE |

Six FHIR Tasks written. Six BigQuery rows. One Pub/Sub message. Zero errors.

---

## What Makes This Hard

A few things that aren't obvious until you're in it:

**The creatinine threshold is specific for a reason.** The KDIGO definition of AKI is a creatinine rise of 1.5x baseline within 7 days, or 0.3 mg/dL absolute increase within 48 hours. The prompt instructs Gemini to require 3+ readings showing an upward trajectory before flagging. A single elevated creatinine doesn't mean AKI — it could be the patient's baseline. Getting this wrong means noisy queries that CDI specialists stop reading.

**ICD-10 specificity is a perpetual battle.** Without explicit instruction, Gemini returns family codes (E11, N17) instead of specific codes (E11.9, N17.9). I added a Pydantic validator that rejects known non-billable family codes at the model level, and the CDI-3 prompt explicitly requires codes with decimal qualifiers.

**The existing diagnosis suppression has to happen twice.** The CDI-3 prompt shows Gemini what's already coded so it can note obvious overlaps. CDI-4 then does the definitive cross-reference. One pass isn't enough — Gemini sometimes signals for conditions that are clearly documented if it hasn't been explicitly told to check first.

**The Google Cloud Healthcare API doesn't support `$everything` on Encounter resources.** This took one failed deploy to discover. The workaround — individual searches by resource type with `?encounter=Encounter/{id}` — actually gives more control over what gets loaded and works cleanly with pagination.

---

## The ADK Web UI

One of the reasons I chose Google ADK for this portfolio is the built-in Web UI. Running `adk web agents` spins up a local server where you can watch the agent work in real time — tool calls, responses, trace view, session history.

For a clinical application, the trace view is particularly useful. You can see exactly which signals were identified, which were resolved, which crossed the confidence threshold, and what queries were generated — all before a single physician gets pinged.

---

## What's Next

HC-6 is the midpoint of the nine-project healthcare series. The remaining projects cover:

- **HC-7**: Prior Authorization Automation (payer rules engine)
- **HC-8**: Clinical Trial Matching Agent
- **HC-9**: Healthcare Operations Command Center (multi-agent)

The full portfolio — 21 projects across healthcare, security, retail, financial services, insurance, and legal — is building toward an AI/ML Architect role at a company doing serious applied AI in a regulated domain.

The code is at [github.com/gbhorne/adk-cdi-agent](https://github.com/gbhorne/adk-cdi-agent).

---

*Gregory Horne is a GCP Cloud Architect and AI/ML Engineer based in Atlanta. This is project 6 of a 21-project agentic AI portfolio.*
