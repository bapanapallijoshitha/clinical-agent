import json
import os
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator import Orchestrator
from tools import get_all_patient_data
from validator import validate_record
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

app = FastAPI(title="Clinical Documentation & Follow-up Agent", version="2.0.0")
app.mount("/static", StaticFiles(directory=ROOT_DIR), name="static")


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


def load_scenarios_data() -> dict:
    scen_path = DATA_DIR / "scenarios.json"
    if scen_path.exists():
        with open(scen_path, encoding="utf-8") as f:
            return json.load(f)
    return {"scenarios": []}


def get_scenario_by_id(scenario_id: str) -> Optional[dict]:
    data = load_scenarios_data()
    for s in data.get("scenarios", []):
        if s.get("id") == scenario_id or s.get("patient_id") == scenario_id:
            return s
    return None


def load_transcript():
    with open(DATA_DIR / "transcript.json", encoding="utf-8") as f:
        return json.load(f)


def clean_output(record_obj):
    validation = record_obj.get("_validation", {"status": "UNKNOWN", "issues": []})
    return {
        "patient_id": record_obj.get("patient_id"),
        "visit_date": record_obj.get("visit_date"),
        "record": record_obj.get("record", {}),
        "unresolved_for_clinician_review": record_obj.get("unresolved_for_clinician_review", []),
        "reasoning_log": record_obj.get("reasoning_log", []),
        "validation": validation,
    }


@app.get("/")
def home():
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/styles.css")
def get_css():
    return FileResponse(ROOT_DIR / "styles.css")


@app.get("/app.js")
def get_js():
    return FileResponse(ROOT_DIR / "app.js")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "groq_configured": bool(os.environ.get("GROQ_API_KEY")),
        "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
    }


@app.get("/api/scenarios")
def list_scenarios():
    data = load_scenarios_data()
    summaries = []
    for s in data.get("scenarios", []):
        summaries.append({
            "id": s.get("id"),
            "patient_id": s.get("patient_id"),
            "title": s.get("title"),
            "category": s.get("category"),
            "difficulty": s.get("difficulty"),
            "badge": s.get("badge"),
            "summary": s.get("summary"),
            "challenge": s.get("challenge"),
            "visit_date": s.get("visit_date"),
        })
    return {"scenarios": summaries}


@app.get("/api/scenario/{scenario_id}")
def get_scenario(scenario_id: str):
    scen = get_scenario_by_id(scenario_id)
    if not scen:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")
    return scen


@app.get("/api/patient-data/{patient_id}")
def get_patient_data_endpoint(patient_id: str):
    data = get_all_patient_data(patient_id)
    return data


@app.get("/api/guidelines")
def get_guidelines():
    guide_path = DATA_DIR / "guidelines.json"
    if guide_path.exists():
        with open(guide_path, encoding="utf-8") as f:
            return json.load(f)
    return []


@app.get("/api/demo-data")
def demo_data():
    t = load_transcript()
    return {
        "patient_id": t["patient_id"],
        "visit_date": t["visit_date"],
        "turns": t["turns"],
        "late_update": t.get("late_update"),
    }


@app.post("/api/run")
def run_agent(body: Optional[RunRequest] = None):
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY is not configured in the environment variables.",
        )
    try:
        scenario = None
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
        elif body and body.transcript:
            patient_id = body.patient_id or body.transcript.get("patient_id", "CUSTOM-01")
            visit_date = body.visit_date or body.transcript.get("visit_date", "2026-10-25")
            transcript = body.transcript
            transcript["patient_id"] = patient_id
            transcript["visit_date"] = visit_date
            patient_context = body.custom_data or {
                "patient_id": patient_id,
                "medications": (body.custom_data or {}).get("medications", []),
                "allergies": (body.custom_data or {}).get("allergies", []),
                "labs": (body.custom_data or {}).get("labs", []),
                "prior_notes": (body.custom_data or {}).get("prior_notes", []),
            }
        else:
            # Default fallback
            t = load_transcript()
            patient_id = t["patient_id"]
            visit_date = t["visit_date"]
            transcript = t
            patient_context = get_all_patient_data(patient_id)

        events = []
        orch = Orchestrator(
            patient_id=patient_id,
            transcript=transcript,
            event_callback=events.append,
            patient_context=patient_context,
        )
        result = orch.draft()
        output = clean_output({**result, "patient_id": patient_id, "visit_date": visit_date})
        return {
            "phase": "draft",
            "scenario_id": body.scenario_id if body else None,
            "patient_id": patient_id,
            "output": output,
            "events": events,
            "late_update": transcript.get("late_update"),
        }
    except Exception as exc:
        traceback.print_exc()  # print full traceback to terminal
        raise HTTPException(status_code=500, detail=f"Server error: {type(exc).__name__}: {exc}") from exc


@app.post("/api/adapt")
def adapt_agent(body: AdaptRequest):
    if not os.environ.get("GROQ_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY is not configured in the environment variables.",
        )
    try:
        scenario = None
        if body.scenario_id:
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
            late_text = body.text or scenario.get("late_update", {}).get("text")
        elif body.custom_data and "transcript" in body.custom_data:
            transcript = body.custom_data["transcript"]
            patient_id = transcript.get("patient_id", "CUSTOM-01")
            visit_date = transcript.get("visit_date", "2026-10-25")
            patient_context = body.custom_data
            late_text = body.text or transcript.get("late_update", {}).get("text")
        else:
            t = load_transcript()
            patient_id = t["patient_id"]
            visit_date = t["visit_date"]
            transcript = t
            patient_context = get_all_patient_data(patient_id)
            late_text = body.text or t.get("late_update", {}).get("text")

        if not late_text:
            raise HTTPException(status_code=400, detail="No late update supplied.")

        events = []
        orch = Orchestrator(
            patient_id=patient_id,
            transcript=transcript,
            event_callback=events.append,
            patient_context=patient_context,
        )
        orch.draft()
        result = orch.adapt(late_text)
        output = clean_output({**result, "patient_id": patient_id, "visit_date": visit_date})
        return {
            "phase": "adaptation",
            "scenario_id": body.scenario_id,
            "patient_id": patient_id,
            "output": output,
            "events": events,
            "late_update": {"text": late_text},
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Server error: {type(exc).__name__}: {exc}") from exc


@app.post("/api/test/validate")
def test_validate(candidate: dict):
    """Direct test endpoint to validate an arbitrary clinical record dict."""
    return validate_record(candidate)
