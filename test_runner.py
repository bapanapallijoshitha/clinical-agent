"""
Clinical Agent — Multi-Scenario Test Harness & Benchmark Runner

Runs offline unit & guardrail tests, tool retrieval validation, and mock/live
orchestrator integration tests across all synthetic clinical scenarios.

Usage:
  python test_runner.py                   # Run all offline unit & mock scenario tests
  python test_runner.py --unit            # Run only unit, guardrail, and data integrity tests
  python test_runner.py --scenarios       # Run mock E2E tests for all 5 clinical scenarios
  python test_runner.py --live            # Run live E2E against Groq API (requires GROQ_API_KEY)
  python test_runner.py --scenario 2      # Run specific scenario (1-5)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure local imports work cleanly
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from guardrail import scan_record, scan_text
from validator import validate_record
from tools import (
    lookup_medication,
    lookup_allergy,
    get_labs,
    get_prior_notes,
    guideline_rag_search,
    get_all_patient_data,
    set_active_patient_context,
    reset_active_patient_context,
)

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def header(title: str):
    print(f"\n{BOLD}{CYAN}{'=' * 65}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 65}{RESET}")


def load_scenarios():
    path = ROOT_DIR / "data" / "scenarios.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)["scenarios"]


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.details = []

    def assert_true(self, condition: bool, name: str, detail: str = ""):
        if condition:
            self.passed += 1
            print(f"  {GREEN}✓ PASS{RESET} {name}")
            self.details.append((True, name, detail))
        else:
            self.failed += 1
            print(f"  {RED}✗ FAIL{RESET} {name} {DIM}({detail}){RESET}")
            self.details.append((False, name, detail))

    def warn(self, name: str, detail: str = ""):
        self.warnings += 1
        print(f"  {YELLOW}⚠ WARN{RESET} {name} {DIM}({detail}){RESET}")


def run_unit_and_guardrail_tests() -> TestResult:
    res = TestResult()
    header("1. GUARDRAIL & PRESCRIPTIVE LANGUAGE REGEX TESTS")

    banned_samples = [
        ("start the patient on lisinopril 20mg", "banned 'start patient on'"),
        ("I will prescribe amoxicillin 500mg", "banned 'prescribe'"),
        ("diagnose with severe major depressive disorder", "banned 'diagnose with'"),
        ("discontinue the metformin immediately", "banned 'discontinue'"),
        ("increase the dose to 80mg daily", "banned 'increase dose'"),
        ("decrease the dose of losartan", "banned 'decrease dose'"),
        ("administer epinephrine 0.3mg IM", "banned 'administer'"),
        ("I recommend prescribing alprazolam", "banned 'recommend prescribing'"),
    ]

    for phrase, label in banned_samples:
        hits = scan_text(phrase)
        res.assert_true(len(hits) > 0, f"Catches banned phrase: '{phrase[:35]}...'", label)

    safe_samples = [
        "Recommend clinician review of blood pressure medication regimen.",
        "Advise clinical evaluation for possible dose adjustment by physician.",
        "Suggested monitoring of serum potassium and renal panel in 2 weeks.",
        "Consider consultation with an allergist regarding penicillin tolerance.",
        "Document provisional penicillin allergy pending clinician confirmation.",
    ]

    for phrase in safe_samples:
        hits = scan_text(phrase)
        res.assert_true(len(hits) == 0, f"Allows compliant phrase: '{phrase[:40]}...'", "safe phrasing")

    # Patient requests must remain documentable without being treated as an
    # agent-authored recommendation. Unsafe clinician-authored text still fails.
    reported_request = {
        "record": {
            "subjective": (
                "Patient reports severe anxiety. Patient-reported transcript (verbatim): "
                "You need to prescribe me Xanax right away."
            ),
            "objective": {},
            "status_summary": "Patient request documented for clinician review.",
            "plan_actions": ["Escalate medication request for licensed clinician review."],
        },
        "unresolved_for_clinician_review": [],
        "reasoning_log": [{"step": "document", "action": "Recorded patient-reported request verbatim."}],
    }
    reported_result = validate_record(reported_request)
    res.assert_true(
        reported_result["status"] == "PASS",
        "Allows explicitly marked verbatim patient request",
        "patient-reported language is not agent-authored advice",
    )

    authored_request = {
        "record": {
            "subjective": "Patient reports severe anxiety.",
            "objective": {},
            "status_summary": "Patient needs medication review.",
            "plan_actions": ["Prescribe Xanax right away."],
        },
        "unresolved_for_clinician_review": [],
        "reasoning_log": [{"step": "test", "action": "Unsafe recommendation test case."}],
    }
    authored_result = validate_record(authored_request)
    res.assert_true(
        authored_result["status"] == "FAIL",
        "Still rejects agent-authored prescribing language",
        "guardrail remains active",
    )

    header("2. STRUCTURAL VALIDATOR RULES TESTS")

    # Valid candidate
    valid_record = {
        "record": {
            "subjective": "Patient reports morning dizziness.",
            "objective": {"potassium": "5.3", "creatinine": "1.3", "egfr": "58"},
            "status_summary": "Stable hypertension with borderline renal indices.",
            "plan_actions": ["Recommend clinician review of potassium levels and ACE inhibitor monitoring."],
        },
        "unresolved_for_clinician_review": [{"issue": "Allergy ambiguity", "detail": "Clarified penicillin vs amoxicillin"}],
        "reasoning_log": [{"step": "plan", "action": "Retrieved labs and evaluated hyperkalemia."}],
    }
    v_res = validate_record(valid_record)
    res.assert_true(v_res["status"] == "PASS", "Valid candidate passes validation", "")

    # Missing field
    invalid_missing = {
        "record": {
            "subjective": "Patient reports dizziness.",
            "objective": {},
            # Missing status_summary
            "plan_actions": ["Review medication"],
        },
        "unresolved_for_clinician_review": [],
        "reasoning_log": [{"step": "1", "action": "test"}],
    }
    v_res2 = validate_record(invalid_missing)
    res.assert_true(v_res2["status"] == "FAIL", "Catches missing required record field", "status_summary missing")

    # Guardrail injection into plan_actions
    poisoned_record = {
        "record": {
            "subjective": "Patient reports pain.",
            "objective": {"inr": "3.8"},
            "status_summary": "Warfarin patient took ibuprofen.",
            "plan_actions": ["Prescribe 10mg vitamin K and discontinue warfarin right now."],
        },
        "unresolved_for_clinician_review": [],
        "reasoning_log": [{"step": "1", "action": "test"}],
    }
    v_res3 = validate_record(poisoned_record)
    res.assert_true(v_res3["status"] == "FAIL", "Validator rejects record containing prescriptive commands", "guardrail trip")

    header("3. CLINICAL DATA INTEGRITY & SANDBOX TOOLS TESTS")

    scenarios = load_scenarios()
    res.assert_true(len(scenarios) >= 5, f"Loaded {len(scenarios)} clinical scenarios", "scenarios.json")

    for s in scenarios:
        pid = s["patient_id"]
        title = s["title"]
        chart = get_all_patient_data(pid)
        res.assert_true(
            len(chart["medications"]) > 0 or len(chart["allergies"]) > 0 or len(chart["labs"]) > 0,
            f"Patient {pid} ({title}) has accessible chart records",
            f"meds: {len(chart['medications'])}, labs: {len(chart['labs'])}",
        )

    # Tool checks
    med_lookup = lookup_medication("Warfarin")
    res.assert_true(
        med_lookup["found_in_active_medications"] and len(med_lookup["known_interaction_notes"]) > 0,
        "lookup_medication('Warfarin') finds active drug and high-risk interactions",
        "",
    )

    allergy_lookup = lookup_allergy("SYN-0218")
    allergies = allergy_lookup.get("documented_allergies", [])
    has_penicillin = any("penicillin" in a.get("allergen", "").lower() for a in (allergies or []))
    res.assert_true(has_penicillin, "lookup_allergy('SYN-0218') identifies life-threatening penicillin anaphylaxis", "")

    lab_lookup = get_labs("SYN-0344", panel="Metabolic")
    panels = lab_lookup.get("panels", [])
    has_aki = any(p.get("results", {}).get("creatinine", 0) >= 1.5 for p in panels)
    res.assert_true(has_aki, "get_labs('SYN-0344') retrieves acute kidney injury panel (Cr >= 1.5)", "")

    rag_lookup = guideline_rag_search("warfarin nsaid bleeding inr")
    res.assert_true(
        len(rag_lookup["results"]) > 0 and any("warfarin" in r["topic"].lower() for r in rag_lookup["results"]),
        "guideline_rag_search matches anticoagulation bleeding guideline snippet",
        "",
    )

    return res


def run_mock_scenario_pipeline(scenario_num: int = None) -> TestResult:
    res = TestResult()
    header("4. END-TO-END SCENARIO SIMULATION PIPELINE")
    scenarios = load_scenarios()

    if scenario_num:
        scenarios = [scenarios[scenario_num - 1]]

    for i, s in enumerate(scenarios, 1):
        pid = s["patient_id"]
        title = s["title"]
        print(f"\n{BOLD}▶ Testing Scenario {i}: [{pid}] {title}{RESET}")
        print(f"  {DIM}Category: {s['category']} | Challenge: {s['challenge']}{RESET}")

        start_time = time.time()

        # Step 1: Set context
        token = set_active_patient_context({
            "patient_id": pid,
            "medications": s.get("medications", []),
            "allergies": s.get("allergies", []),
            "labs": s.get("labs", []),
            "prior_notes": s.get("prior_notes", []),
        })

        try:
            # Step 2: Validate tool queries for this scenario
            tools_called = []

            # Simulate tool calls model would make
            if "Warfarin" in str(s["medications"]):
                t1 = lookup_medication("Warfarin")
                tools_called.append(("lookup_medication", t1))
            if s.get("allergies"):
                t2 = lookup_allergy(pid)
                tools_called.append(("lookup_allergy", t2))
            if s.get("labs"):
                t3 = get_labs(pid)
                tools_called.append(("get_labs", t3))

            rag_query = f"{s['category']} {s['title']}"
            t4 = guideline_rag_search(rag_query)
            tools_called.append(("guideline_rag_search", t4))

            res.assert_true(
                len(tools_called) >= 2,
                f"Scenario {i}: Successfully executed {len(tools_called)} simulated evidence retrieval tool calls",
                "",
            )

            # Step 3: Construct realistic draft output based on scenario evidence
            simulated_draft = {
                "record": {
                    "subjective": (
                        f"Consultation review for {pid}. Patient presented for follow-up. "
                        f"Patient-reported transcript (verbatim): {s['transcript']['turns'][1]['text']}"
                    ),
                    "objective": {
                        "patient_id": pid,
                        "primary_med": s["medications"][0]["name"] if s["medications"] else "None",
                        "lab_status": "Monitored",
                    },
                    "status_summary": f"Clinical follow-up for {s['category']}. Monitored evidence retrieved via sandbox tools.",
                    "plan_actions": [
                        f"Recommend clinician review of {s['medications'][0]['name'] if s['medications'] else 'regimen'} and follow-up.",
                        "Advise laboratory monitoring and patient counseling on symptoms.",
                    ],
                },
                "unresolved_for_clinician_review": [
                    {
                        "issue": s["title"],
                        "detail": s["challenge"],
                        "source_conflict": ["Patient transcript", "Retrieved clinical records"],
                    }
                ],
                "reasoning_log": [
                    {"step": "plan", "action": f"Verified clinical facts for {pid}."},
                    {"step": "gather", "action": f"Called sandbox tools ({len(tools_called)} queries executed)."},
                    {"step": "reconcile", "action": "Synthesized evidence and checked safety guidelines."},
                ],
            }

            v_draft = validate_record(simulated_draft)
            res.assert_true(
                v_draft["status"] == "PASS",
                f"Scenario {i}: Draft structured record satisfies all validation & guardrail checks",
                "",
            )

            # Step 4: Test adaptation with late-arriving update
            late_text = s.get("late_update", {}).get("text", "")
            if late_text:
                simulated_adapted = json.loads(json.dumps(simulated_draft))
                simulated_adapted["record"]["status_summary"] += f" Late update noted: {late_text[:60]}..."
                simulated_adapted["reasoning_log"].append({
                    "step": "adapt",
                    "action": f"Appended late-arriving portal update: {late_text[:40]}...",
                })
                v_adapt = validate_record(simulated_adapted)
                res.assert_true(
                    v_adapt["status"] == "PASS",
                    f"Scenario {i}: Adapted record with late-arriving update passes validation",
                    "",
                )

            elapsed = time.time() - start_time
            print(f"  {DIM}Scenario {i} simulation completed in {elapsed:.3f}s{RESET}")

        finally:
            reset_active_patient_context(token)

    return res


def run_live_groq_test(scenario_num: int = 1) -> TestResult:
    res = TestResult()
    header(f"5. LIVE ORCHESTRATOR TEST WITH GROQ API (Scenario {scenario_num})")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print(f"  {RED}GROQ_API_KEY environment variable is not set! Skipping live API test.{RESET}")
        print(f"  {YELLOW}To run live tests: set GROQ_API_KEY=your_key and rerun.{RESET}")
        res.warn("Live Groq Test", "GROQ_API_KEY missing")
        return res

    from orchestrator import Orchestrator

    scenarios = load_scenarios()
    s = scenarios[scenario_num - 1]
    pid = s["patient_id"]

    print(f"  {CYAN}Running live agent for Patient {pid}: {s['title']}...{RESET}")
    start_time = time.time()

    events = []
    orch = Orchestrator(
        patient_id=pid,
        transcript={
            "patient_id": pid,
            "visit_date": s["visit_date"],
            "turns": s["transcript"]["turns"],
            "late_update": s.get("late_update"),
        },
        api_key=api_key,
        event_callback=lambda e: events.append(e),
        patient_context={
            "patient_id": pid,
            "medications": s.get("medications", []),
            "allergies": s.get("allergies", []),
            "labs": s.get("labs", []),
            "prior_notes": s.get("prior_notes", []),
        },
    )

    try:
        draft_record = orch.draft()
        res.assert_true(
            draft_record is not None and "_validation" in draft_record,
            "Live draft() returned structured record",
            "",
        )
        res.assert_true(
            draft_record["_validation"]["status"] == "PASS",
            f"Live draft passed validation with status: {draft_record['_validation']['status']}",
            "",
        )

        late_text = s.get("late_update", {}).get("text")
        if late_text:
            print(f"  {CYAN}Running live adapt() with late update...{RESET}")
            adapted = orch.adapt(late_text)
            res.assert_true(
                adapted is not None and adapted["_validation"]["status"] == "PASS",
                "Live adapt() updated record and passed validation",
                "",
            )

        elapsed = time.time() - start_time
        print(f"  {GREEN}Live test completed successfully in {elapsed:.2f}s ({len(events)} agent events emitted){RESET}")

    except Exception as exc:
        res.assert_true(False, "Live orchestrator run encountered an error", str(exc))

    return res


def main():
    parser = argparse.ArgumentParser(description="Clinical Agent Test Harness")
    parser.add_argument("--unit", action="store_true", help="Run only unit, guardrail, and data integrity tests")
    parser.add_argument("--scenarios", action="store_true", help="Run simulated pipeline for all scenarios")
    parser.add_argument("--live", action="store_true", help="Run live E2E against Groq API")
    parser.add_argument("--scenario", type=int, default=None, help="Select scenario number (1-5)")
    args = parser.parse_args()

    start_total = time.time()
    total_passed = 0
    total_failed = 0
    total_warnings = 0

    run_all = not (args.unit or args.scenarios or args.live)

    if run_all or args.unit:
        r1 = run_unit_and_guardrail_tests()
        total_passed += r1.passed
        total_failed += r1.failed
        total_warnings += r1.warnings

    if run_all or args.scenarios:
        r2 = run_mock_scenario_pipeline(args.scenario)
        total_passed += r2.passed
        total_failed += r2.failed
        total_warnings += r2.warnings

    if args.live:
        r3 = run_live_groq_test(args.scenario or 1)
        total_passed += r3.passed
        total_failed += r3.failed
        total_warnings += r3.warnings

    total_time = time.time() - start_total

    header("TEST RUNNER SUMMARY")
    status_str = f"{GREEN}ALL PASSED{RESET}" if total_failed == 0 else f"{RED}{total_failed} FAILED{RESET}"
    print(f"  Result:      {status_str}")
    print(f"  Tests Run:   {BOLD}{total_passed + total_failed}{RESET} total ({total_passed} passed, {total_failed} failed, {total_warnings} warnings)")
    print(f"  Duration:    {total_time:.3f} seconds")
    print(f"{BOLD}{CYAN}{'=' * 65}{RESET}\n")

    sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    main()
