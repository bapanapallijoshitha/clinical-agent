"""
Structural/safety validator for a drafted record. Deliberately rule-based
(not another free-form LLM opinion) so PASS/FAIL is reproducible and testable.
"""
from guardrail import scan_record

REQUIRED_RECORD_FIELDS = ["subjective", "objective", "status_summary", "plan_actions"]
REQUIRED_TOP_FIELDS = ["record", "unresolved_for_clinician_review", "reasoning_log"]


def validate_record(candidate: dict) -> dict:
    """
    candidate: dict with keys record, unresolved_for_clinician_review, reasoning_log
    Returns {"status": "PASS"|"FAIL", "issues": [...]}
    """
    issues = []

    for field in REQUIRED_TOP_FIELDS:
        if field not in candidate:
            issues.append({"check": "structure", "detail": f"missing top-level field '{field}'"})

    record = candidate.get("record", {})
    for field in REQUIRED_RECORD_FIELDS:
        if field not in record or record[field] in (None, "", []):
            issues.append({"check": "structure", "detail": f"record missing or empty field '{field}'"})

    if not isinstance(candidate.get("plan_actions", record.get("plan_actions")), list):
        pass  # plan_actions lives under record; covered above

    if not isinstance(candidate.get("unresolved_for_clinician_review", []), list):
        issues.append({"check": "structure", "detail": "unresolved_for_clinician_review must be a list (can be empty)"})

    if not isinstance(candidate.get("reasoning_log", []), list) or len(candidate.get("reasoning_log", [])) == 0:
        issues.append({"check": "structure", "detail": "reasoning_log must be a non-empty list"})

    guardrail_hits = scan_record(record)
    for hit in guardrail_hits:
        issues.append({"check": "guardrail", "detail": f"{hit['field']}: '{hit['phrase']}' - {hit['reason']}"})

    action_list = record.get("plan_actions", [])
    if isinstance(action_list, list):
        for i, a in enumerate(action_list):
            if not isinstance(a, str) or len(a.strip()) < 5:
                issues.append({"check": "content", "detail": f"plan_actions[{i}] is empty or not meaningful text"})

    status = "PASS" if not issues else "FAIL"
    return {"status": status, "issues": issues}
