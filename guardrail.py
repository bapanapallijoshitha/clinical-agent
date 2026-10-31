"""
Hard guardrail: scans drafted text for prescriptive/diagnostic language.
This runs as a rules-based post-filter, independent of the drafting model,
so the constraint is a system property rather than something only asked for
in a prompt.
"""
import re

BANNED_PATTERNS = [
    r"\bstart(ed|ing)?\s+(the\s+)?patient\s+on\b",
    r"\bprescrib(e|ed|ing)\b",
    r"\bdiagnos(e|ed|is|ing)\s*(with|of)?\b",
    r"\bdiscontinue\b",
    r"\bincrease\s+(the\s+)?dose\b",
    r"\bdecrease\s+(the\s+)?dose\b",
    r"\badminister(ed|ing)?\b",
    r"\bi\s+(recommend|advise)\s+(starting|stopping|prescribing)\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]


def scan_text(text: str) -> list:
    """Return list of banned phrase matches found in text."""
    if not text:
        return []
    hits = []
    for pattern in _COMPILED:
        for m in pattern.finditer(text):
            hits.append(m.group(0))
    return hits


def scan_record(record: dict) -> list:
    """Scan the clinically-authored free-text fields of a record for
    prescriptive/diagnostic language. Returns a list of issue dicts."""
    issues = []
    fields_to_scan = {
        "subjective": record.get("subjective", ""),
        "status_summary": record.get("status_summary", ""),
    }
    for name, text in fields_to_scan.items():
        # Patient quotations are evidence to document, not instructions authored
        # by the agent. The orchestrator must introduce verbatim transcript text
        # with this explicit marker; only the agent-authored prefix is scanned.
        if name == "subjective" and isinstance(text, str):
            marker = "Patient-reported transcript (verbatim):"
            text = text.split(marker, 1)[0]
        hits = scan_text(text)
        for h in hits:
            issues.append({"field": name, "phrase": h,
                            "reason": "prescriptive/diagnostic language is not permitted"})

    for i, action in enumerate(record.get("plan_actions", [])):
        hits = scan_text(action)
        for h in hits:
            issues.append({"field": f"plan_actions[{i}]", "phrase": h,
                            "reason": "prescriptive/diagnostic language is not permitted"})
    return issues
