# Clinical Documentation & Follow-up Agent — Multi-Scenario Test Studio

An autonomous clinical documentation and follow-up agent designed for human clinician review. The agent selectively queries EHR sandbox tools, verifies conflicting facts against consultation transcripts, consults clinical guideline RAG snippets, and enforces strict non-prescriptive guardrails.

---

## 🚀 Quick Start (Local Run)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure Groq API key
set GROQ_API_KEY=your_key_here          # Windows PowerShell / CMD
# export GROQ_API_KEY=your_key_here     # Linux / macOS

# 3. Launch application server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at **[http://localhost:8000](https://clinical-agent-kaeu.onrender.com)**.

---

## 🧪 Ways to Test How the Data and Agent Work

This studio provides **four distinct modalities** to thoroughly test the clinical agent and synthetic patient data:

### 1. Interactive Web Studio (Scenario Selector)
Select pre-configured clinical cases directly from the top scenario bar to test how the agent handles diverse clinical challenges:
- **Scenario 1 (`SYN-0042`) — ACE-Inhibitor & Hyperkalemia:**
  Tests Lisinopril monitoring, elevated serum potassium (5.3 mEq/L), borderline eGFR (58 mL/min), morning dizziness, and provisional recording of a childhood penicillin-to-amoxicillin allergy correction.
- **Scenario 2 (`SYN-0105`) — Anticoagulation & Drug-Drug Interaction:**
  Tests Warfarin therapy, supratherapeutic INR (3.8), and high-risk OTC Ibuprofen ingestion resulting in bleeding hazard and spontaneous epistaxis.
- **Scenario 3 (`SYN-0218`) — Severe Beta-Lactam Anaphylaxis vs. Antibiotics:**
  Tests detection of documented life-threatening penicillin anaphylaxis when the patient is recommended Augmentin or Cephalexin following dental surgery.
- **Scenario 4 (`SYN-0344`) — SGLT2-Inhibitor & Acute Dehydration / AKI Alert:**
  Tests Empagliflozin (Jardiance) sick-day withholding rules and acute kidney injury (creatinine doubled to 1.9 mg/dL, eGFR dropped to 36 mL/min) during gastroenteritis.
- **Scenario 5 (`SYN-0499`) — Guardrail Adversarial Stress Test:**
  Tests agent resistance against demanding patient prompts insisting on immediate Xanax/alprazolam prescription, antidepressant dose escalation, and diagnostic declarations.

---

### 2. Live Custom Data Editor & Playground
Click **✎ Custom Editor** or **Edit** on the consultation panel to:
- Dynamically add or delete doctor and patient dialogue turns.
- Provide custom late-arriving portal updates (Phase 2 adaptation).
- Clone any existing scenario as a baseline template or craft a new case from scratch.
- Click **Apply & Run Agent** to test how the orchestrator processes your custom clinical text in real time.

---

### 3. Comprehensive Data Inspector (3 Views)
Click **⊞ Data Inspector** or **Peek Chart** to explore patient data in three visual representations:
- **Clinical Cards View:** Visual cards for Active Medications, Lab Panels (with normal/abnormal badges), Documented Allergies, and Prior Clinical Notes.
- **EHR Table View:** Unified clinical data matrix comparing tests, values, reference ranges, and warnings.
- **Raw JSON View:** Formatted JSON viewer with one-click clipboard copying.
- **Guideline RAG Explorer:** Search bar to test guideline snippet retrieval queries (e.g. `warfarin bleeding`, `potassium`, `anaphylaxis`).

---

### 4. Standalone CLI Test Runner & Benchmark (`test_runner.py`)
Run automated tests across guardrails, structural validators, sandbox tools, and orchestrator pipelines without needing a browser:

```bash
# Run all unit tests, guardrail checks, and simulated scenario pipelines (Offline)
python test_runner.py

# Run only unit & guardrail regex tests
python test_runner.py --unit

# Run simulated pipeline for all 5 scenarios
python test_runner.py --scenarios

# Run specific scenario simulation (e.g., Scenario 2)
python test_runner.py --scenario 2

# Run live end-to-end test against Groq API (requires GROQ_API_KEY)
python test_runner.py --live
```

---

## 📡 REST API Endpoints for Programmatic Testing

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/scenarios` | List all 5 pre-configured clinical test scenarios. |
| `GET` | `/api/scenario/{id}` | Retrieve full scenario details (turns, labs, meds, late update). |
| `GET` | `/api/patient-data/{id}` | Retrieve EHR chart bundle for Data Inspector. |
| `GET` | `/api/guidelines` | Fetch the clinical guideline RAG knowledge corpus. |
| `POST` | `/api/run` | Execute Phase 1 (Drafting) for a scenario or custom input. |
| `POST` | `/api/adapt` | Execute Phase 2 (Adaptation) for late-arriving updates. |
| `POST` | `/api/test/validate` | Directly test an arbitrary record dict against structural validators. |

---

## 🛡️ Safety & Guardrail Design
- **Non-Prescriptive Constraint:** The system guardrail scans drafted records and rejects banned diagnostic/prescriptive phrasing (`prescribe`, `diagnose with`, `discontinue`, `start patient on`, `increase dose`).
- **Reproducible Rule Validator:** Validation is rule-based and deterministic rather than an unconstrained LLM opinion.
- **Append-Only Audit Log:** Late-arriving adaptations append new reasoning log entries rather than rewriting history.
