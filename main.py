import json
import os
import traceback
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator import Orchestrator
from tools import get_all_patient_data
from validator import validate_record
from dotenv import load_dotenv


# Load environment variables from .env locally
load_dotenv()


# --------------------------------------------------
# PATH CONFIGURATION
# --------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = ROOT_DIR / "static"


# --------------------------------------------------
# FASTAPI APPLICATION
# --------------------------------------------------

app = FastAPI(
    title="Clinical Documentation & Follow-up Agent",
    version="2.0.0"
)


# --------------------------------------------------
# STATIC FILES
# Serves:
# /static/styles.css
# /static/app.js
# --------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static"
)


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class RunRequest(BaseModel):
    scenario_id: Optional[str] = None
    patient_id: Optional[str] = None
    visit_date: Optional[str] = None
    transcript: Optional[Dict[str, Any]] = None
    custom_data: Optional[Dict[str, Any]] = None


class AdaptRequest(BaseModel):
    scenario_id: Optional[str] = None
    text: Optional[str] = None
    custom_data: Optional[Dict[str, Any]] = None


# --------------------------------------------------
# DATA FUNCTIONS
# --------------------------------------------------

def load_scenarios_data() -> dict:
    scen_path = DATA_DIR / "scenarios.json"

    if scen_path.exists():
        with open(scen_path, encoding="utf-8") as f:
            return json.load(f)

    return {"scenarios": []}


def get_scenario_by_id(scenario_id: str) -> Optional[dict]:
    data = load_scenarios_data()

    for scenario in data.get("scenarios", []):
        if (
            scenario.get("id") == scenario_id
            or scenario.get("patient_id") == scenario_id
        ):
            return scenario

    return None


def load_transcript() -> dict:
    with open(DATA_DIR / "transcript.json", encoding="utf-8") as f:
        return json.load(f)


def clean_output(record_obj: dict) -> dict:
    validation = record_obj.get(
        "_validation",
        {
            "status": "UNKNOWN",
            "issues": []
        }
    )

    return {
        "patient_id": record_obj.get("patient_id"),
        "visit_date": record_obj.get("visit_date"),
        "record": record_obj.get("record", {}),
        "unresolved_for_clinician_review": record_obj.get(
            "unresolved_for_clinician_review",
            []
        ),
        "reasoning_log": record_obj.get(
            "reasoning_log",
            []
        ),
        "validation": validation,
    }


# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.get("/")
def home():
    return FileResponse(ROOT_DIR / "index.html")


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "groq_configured": bool(
            os.environ.get("GROQ_API_KEY")
        ),
        "model": os.environ.get(
            "GROQ_MODEL",
            "openai/gpt-oss-120b"
        ),
    }


# --------------------------------------------------
# GET ALL SCENARIOS
# --------------------------------------------------

@app.get("/api/scenarios")
def list_scenarios():
    data = load_scenarios_data()
    summaries = []

    for scenario in data.get("scenarios", []):
        summaries.append({
            "id": scenario.get("id"),
            "patient_id": scenario.get("patient_id"),
            "title": scenario.get("title"),
            "category": scenario.get("category"),
            "difficulty": scenario.get("difficulty"),
            "badge": scenario.get("badge"),
            "summary": scenario.get("summary"),
            "challenge": scenario.get("challenge"),
            "visit_date": scenario.get("visit_date"),
        })

    return {
        "scenarios": summaries
    }


# --------------------------------------------------
# GET SINGLE SCENARIO
# --------------------------------------------------

@app.get("/api/scenario/{scenario_id}")
def get_scenario(scenario_id: str):
    scenario = get_scenario_by_id(scenario_id)

    if not scenario:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario '{scenario_id}' not found."
        )

    return scenario


# --------------------------------------------------
# GET PATIENT DATA
# --------------------------------------------------

@app.get("/api/patient-data/{patient_id}")
def get_patient_data_endpoint(patient_id: str):
    return get_all_patient_data(patient_id)


# --------------------------------------------------
# GET GUIDELINES
# --------------------------------------------------

@app.get("/api/guidelines")
def get_guidelines():
    guide_path = DATA_DIR / "guidelines.json"

    if guide_path.exists():
        with open(guide_path, encoding="utf-8") as f:
            return json.load(f)

    return []


# --------------------------------------------------
# GET DEFAULT DEMO DATA
# --------------------------------------------------

@app.get("/api/demo-data")
def demo_data():
    transcript = load_transcript()

    return {
        "patient_id": transcript["patient_id"],
        "visit_date": transcript["visit_date"],
        "turns": transcript["turns"],
        "late_update": transcript.get("late_update"),
    }


# --------------------------------------------------
# RUN AGENT — PHASE 1
# --------------------------------------------------

@app.post("/api/run")
def run_agent(body: Optional[RunRequest] = None):

    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail=(
                "GROQ_API_KEY is not configured "
                "in the environment variables."
            ),
        )

    try:
        scenario = None

        # ------------------------------------------
        # CASE 1: PREDEFINED SCENARIO
        # ------------------------------------------

        if body and body.scenario_id:
            scenario = get_scenario_by_id(body.scenario_id)

        if scenario:

            patient_id = scenario["patient_id"]
            visit_date = scenario["visit_date"]

            transcript = {
                "patient_id": patient_id,
                "visit_date": visit_date,
                "turns": scenario["transcript"]["turns"],
                "late_update": scenario.get("late_update"),
            }

            patient_context = {
                "patient_id": patient_id,
                "medications": scenario.get("medications", []),
                "allergies": scenario.get("allergies", []),
                "labs": scenario.get("labs", []),
                "prior_notes": scenario.get("prior_notes", []),
            }

        # ------------------------------------------
        # CASE 2: CUSTOM TRANSCRIPT
        # ------------------------------------------

        elif body and body.transcript:

            patient_id = (
                body.patient_id
                or body.transcript.get("patient_id", "CUSTOM-01")
            )

            visit_date = (
                body.visit_date
                or body.transcript.get("visit_date", "2026-10-25")
            )

            transcript = body.transcript.copy()

            transcript["patient_id"] = patient_id
            transcript["visit_date"] = visit_date

            patient_context = body.custom_data or {
                "patient_id": patient_id,
                "medications": [],
                "allergies": [],
                "labs": [],
                "prior_notes": [],
            }

        # ------------------------------------------
        # CASE 3: DEFAULT DEMO DATA
        # ------------------------------------------

        else:

            transcript = load_transcript()

            patient_id = transcript["patient_id"]
            visit_date = transcript["visit_date"]

            patient_context = get_all_patient_data(patient_id)

        # ------------------------------------------
        # RUN ORCHESTRATOR
        # ------------------------------------------

        events = []

        orchestrator = Orchestrator(
            patient_id=patient_id,
            transcript=transcript,
            event_callback=events.append,
            patient_context=patient_context,
        )

        result = orchestrator.draft()

        output = clean_output({
            **result,
            "patient_id": patient_id,
            "visit_date": visit_date,
        })

        return {
            "phase": "draft",
            "scenario_id": body.scenario_id if body else None,
            "patient_id": patient_id,
            "output": output,
            "events": events,
            "late_update": transcript.get("late_update"),
        }

    except Exception as exc:

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                f"Server error: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


# --------------------------------------------------
# ADAPT AGENT — PHASE 2
# --------------------------------------------------

@app.post("/api/adapt")
def adapt_agent(body: AdaptRequest):

    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail=(
                "GROQ_API_KEY is not configured "
                "in the environment variables."
            ),
        )

    try:
        scenario = None

        if body.scenario_id:
            scenario = get_scenario_by_id(body.scenario_id)

        # ------------------------------------------
        # PREDEFINED SCENARIO
        # ------------------------------------------

        if scenario:

            patient_id = scenario["patient_id"]
            visit_date = scenario["visit_date"]

            transcript = {
                "patient_id": patient_id,
                "visit_date": visit_date,
                "turns": scenario["transcript"]["turns"],
                "late_update": scenario.get("late_update"),
            }

            patient_context = {
                "patient_id": patient_id,
                "medications": scenario.get("medications", []),
                "allergies": scenario.get("allergies", []),
                "labs": scenario.get("labs", []),
                "prior_notes": scenario.get("prior_notes", []),
            }

            late_text = (
                body.text
                or scenario.get("late_update", {}).get("text")
            )

        # ------------------------------------------
        # CUSTOM DATA
        # ------------------------------------------

        elif body.custom_data and "transcript" in body.custom_data:

            transcript = body.custom_data["transcript"]

            patient_id = transcript.get(
                "patient_id",
                "CUSTOM-01"
            )

            visit_date = transcript.get(
                "visit_date",
                "2026-10-25"
            )

            patient_context = body.custom_data

            late_text = (
                body.text
                or transcript.get("late_update", {}).get("text")
            )

        # ------------------------------------------
        # DEFAULT DATA
        # ------------------------------------------

        else:

            transcript = load_transcript()

            patient_id = transcript["patient_id"]
            visit_date = transcript["visit_date"]

            patient_context = get_all_patient_data(patient_id)

            late_text = (
                body.text
                or transcript.get("late_update", {}).get("text")
            )

        # ------------------------------------------
        # CHECK LATE UPDATE
        # ------------------------------------------

        if not late_text:
            raise HTTPException(
                status_code=400,
                detail="No late update supplied."
            )

        # ------------------------------------------
        # RUN DRAFT + ADAPTATION
        # ------------------------------------------

        events = []

        orchestrator = Orchestrator(
            patient_id=patient_id,
            transcript=transcript,
            event_callback=events.append,
            patient_context=patient_context,
        )

        orchestrator.draft()

        result = orchestrator.adapt(late_text)

        output = clean_output({
            **result,
            "patient_id": patient_id,
            "visit_date": visit_date,
        })

        return {
            "phase": "adaptation",
            "scenario_id": body.scenario_id,
            "patient_id": patient_id,
            "output": output,
            "events": events,
            "late_update": {
                "text": late_text
            },
        }

    except HTTPException:
        raise

    except Exception as exc:

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                f"Server error: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


# --------------------------------------------------
# DIRECT VALIDATION TEST ENDPOINT
# --------------------------------------------------

@app.post("/api/test/validate")
def test_validate(candidate: dict):
    """Validate an arbitrary clinical record dictionary."""

    return validate_record(candidate)