"""
Deterministic 'sandbox' tools the orchestrator agent can call.
Each function reads from synthetic clinical records or runtime scenario context
and returns structured dicts (never free text) so results are reproducible and verifiable.
"""
import contextvars
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

# Thread-local / async-safe context for custom or scenario-specific runs
_runtime_patient_context = contextvars.ContextVar("runtime_patient_context", default=None)


def set_active_patient_context(context_dict: dict):
    """Set the active patient data bundle for the current execution thread."""
    return _runtime_patient_context.set(context_dict)


def reset_active_patient_context(token):
    """Reset the runtime patient context."""
    if token:
        _runtime_patient_context.reset(token)


def _load_json(filename: str):
    path = DATA_DIR / filename
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _get_scenario_by_patient_id(patient_id: str):
    """Look up a scenario from scenarios.json if available."""
    scenarios_data = _load_json("scenarios.json")
    if scenarios_data and "scenarios" in scenarios_data:
        for s in scenarios_data["scenarios"]:
            if s.get("patient_id") == patient_id:
                return s
    return None


def get_all_patient_data(patient_id: str) -> dict:
    """Fetch complete patient chart bundle (meds, labs, allergies, notes) for a patient."""
    ctx = _runtime_patient_context.get()
    if ctx and ctx.get("patient_id") == patient_id:
        return {
            "patient_id": patient_id,
            "medications": ctx.get("medications", []),
            "allergies": ctx.get("allergies", []),
            "labs": ctx.get("labs", []),
            "prior_notes": ctx.get("prior_notes", []),
        }

    scen = _get_scenario_by_patient_id(patient_id)
    if scen:
        return {
            "patient_id": patient_id,
            "medications": scen.get("medications", []),
            "allergies": scen.get("allergies", []),
            "labs": scen.get("labs", []),
            "prior_notes": scen.get("prior_notes", []),
        }

    # Fallback to single-patient files for backward compatibility
    allergies = _load_json("allergies.json") or {}
    meds = _load_json("medications.json") or {}
    labs = _load_json("labs.json") or {}
    notes = _load_json("prior_notes.json") or {}

    return {
        "patient_id": patient_id,
        "medications": meds.get("active_medications", []) if meds.get("patient_id") == patient_id else [],
        "allergies": allergies.get("documented_allergies", []) if allergies.get("patient_id") == patient_id else [],
        "labs": labs.get("panels", []) if labs.get("patient_id") == patient_id else [],
        "prior_notes": notes.get("notes", []) if notes.get("patient_id") == patient_id else [],
    }


KNOWN_INTERACTIONS = {
    "lisinopril": [
        "ACE inhibitor. Can raise serum potassium; monitor periodically with potassium-sparing diuretics or renal impairment."
    ],
    "metformin": [
        "Biguanide. Renal clearance dependent; review dose if eGFR declines; potential risk of lactic acidosis during severe dehydration."
    ],
    "warfarin": [
        "Oral anticoagulant. Supratherapeutic INR (>3.0) markedly elevates hemorrhage risk; severe interaction with systemic NSAIDs (e.g. Ibuprofen) and aspirin."
    ],
    "ibuprofen": [
        "Non-selective NSAID. Reversibly inhibits platelet aggregation and impairs gastric mucosal defense; co-administration with Warfarin significantly escalates GI bleeding risk."
    ],
    "advil": [
        "Brand of Ibuprofen (NSAID). High bleeding hazard when taken concomitantly with oral anticoagulants like Warfarin."
    ],
    "empagliflozin": [
        "SGLT2 inhibitor. Promotes urinary glucose/sodium excretion; risk of volume contraction, acute kidney injury, and euglycemic DKA during dehydrating illnesses (sick-day precautions apply)."
    ],
    "jardiance": [
        "Brand of Empagliflozin (SGLT2 inhibitor). Hold or clinician review recommended during acute vomiting/dehydration."
    ],
    "losartan": [
        "Angiotensin II receptor blocker (ARB). Promotes vasodilation; can exacerbate hypotension and renal hypoperfusion during acute volume depletion."
    ],
    "augmentin": [
        "Co-amoxiclav (Amoxicillin + Clavulanate potassium). Strictly contraindicated in patients with severe IgE-mediated penicillin allergy / anaphylaxis."
    ],
    "amoxicillin": [
        "Aminopenicillin. Direct contraindication in penicillin-allergic patients."
    ],
    "cephalexin": [
        "First-generation cephalosporin. Cross-reactivity documented in patients with severe penicillin anaphylaxis due to side-chain similarities; warrants allergy review."
    ],
    "sertraline": [
        "SSRI antidepressant. Dose titration and acute therapy adjustments require clinician assessment and monitoring."
    ],
    "omeprazole": [
        "Proton pump inhibitor (PPI). Discontinuation should be evaluated clinically against gastric indication."
    ],
    "atorvastatin": [
        "HMG-CoA reductase inhibitor (statin). Periodic lipid panel and clinical assessment."
    ],
    "amlodipine": [
        "Dihydropyridine calcium channel blocker. Monitor for peripheral edema and orthostatic symptoms."
    ],
    "levothyroxine": [
        "Thyroid hormone replacement. Take on empty stomach; monitor TSH levels."
    ],
}


def lookup_medication(name: str) -> dict:
    """Look up dosing/interaction-relevant info for a medication name."""
    clean_name = name.strip().lower()
    ctx = _runtime_patient_context.get()
    active_meds = []

    if ctx and "medications" in ctx:
        active_meds = ctx["medications"]
    else:
        # Check current scenario or all scenarios
        scenarios_data = _load_json("scenarios.json")
        if scenarios_data and "scenarios" in scenarios_data:
            for s in scenarios_data["scenarios"]:
                active_meds.extend(s.get("medications", []))
        if not active_meds:
            med_file = _load_json("medications.json") or {}
            active_meds = med_file.get("active_medications", [])

    match = next((m for m in active_meds if m.get("name", "").lower() == clean_name), None)

    # Lookup notes for queried name or partial match
    notes = KNOWN_INTERACTIONS.get(clean_name, [])
    if not notes:
        for k, v in KNOWN_INTERACTIONS.items():
            if k in clean_name or clean_name in k:
                notes = v
                break

    return {
        "queried_name": name,
        "found_in_active_medications": match is not None,
        "record": match,
        "known_interaction_notes": notes,
    }


def lookup_allergy(patient_id: str) -> dict:
    """Retrieve documented allergies on file for a patient."""
    ctx = _runtime_patient_context.get()
    if ctx and ctx.get("patient_id") == patient_id:
        return {
            "patient_id": patient_id,
            "documented_allergies": ctx.get("allergies", []),
        }

    scen = _get_scenario_by_patient_id(patient_id)
    if scen:
        return {
            "patient_id": patient_id,
            "documented_allergies": scen.get("allergies", []),
        }

    data = _load_json("allergies.json") or {}
    if data.get("patient_id") == patient_id:
        return {
            "patient_id": patient_id,
            "documented_allergies": data.get("documented_allergies", []),
        }

    return {
        "patient_id": patient_id,
        "documented_allergies": None,
        "error": f"No allergy records found for patient_id {patient_id}",
    }


def get_labs(patient_id: str, panel: str = None) -> dict:
    """Retrieve lab panel results for a patient, optionally filtered by panel name."""
    ctx = _runtime_patient_context.get()
    panels = []
    if ctx and ctx.get("patient_id") == patient_id:
        panels = ctx.get("labs", [])
    else:
        scen = _get_scenario_by_patient_id(patient_id)
        if scen:
            panels = scen.get("labs", [])
        else:
            data = _load_json("labs.json") or {}
            if data.get("patient_id") == patient_id:
                panels = data.get("panels", [])

    if not panels:
        return {"patient_id": patient_id, "panels": [], "error": f"No lab records found for {patient_id}"}

    if panel:
        filtered = [p for p in panels if panel.lower() in p.get("panel", "").lower()]
        return {"patient_id": patient_id, "panels": filtered if filtered else panels}

    return {"patient_id": patient_id, "panels": panels}


def guideline_rag_search(query: str) -> dict:
    """Keyword-overlap search over the clinical guideline snippet corpus."""
    corpus = _load_json("guidelines.json") or []
    q_terms = set(query.lower().replace(",", " ").replace("-", " ").replace("/", " ").split())
    scored = []
    for entry in corpus:
        kw = set(k.lower() for k in entry.get("keywords", []))
        overlap = len(q_terms & kw) + sum(2 for k in kw if k in query.lower())
        # Also check topic
        if any(term in entry.get("topic", "").lower() for term in q_terms):
            overlap += 3
        if overlap > 0:
            scored.append((overlap, entry))

    scored.sort(key=lambda x: -x[0])
    top = [e for _, e in scored[:3]]
    return {"query": query, "results": top}


def get_prior_notes(patient_id: str) -> dict:
    """Retrieve prior visit notes for a patient."""
    ctx = _runtime_patient_context.get()
    if ctx and ctx.get("patient_id") == patient_id:
        return {"patient_id": patient_id, "notes": ctx.get("prior_notes", [])}

    scen = _get_scenario_by_patient_id(patient_id)
    if scen:
        return {"patient_id": patient_id, "notes": scen.get("prior_notes", [])}

    data = _load_json("prior_notes.json") or {}
    if data.get("patient_id") == patient_id:
        return data

    return {"patient_id": patient_id, "notes": [], "error": f"No prior notes found for patient_id {patient_id}"}


TOOL_FUNCTIONS = {
    "lookup_medication": lookup_medication,
    "lookup_allergy": lookup_allergy,
    "get_labs": get_labs,
    "guideline_rag_search": guideline_rag_search,
    "get_prior_notes": get_prior_notes,
}
