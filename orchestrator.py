"""
Orchestrator: the agentic core.

Runs a tool-calling loop against Groq's OpenAI-compatible chat completions
API. The model plans which facts to verify, calls sandbox tools
conditionally, reconciles conflicts, and finalizes its draft by calling the
`submit_final_record` tool (never by just emitting prose) so the output is
always structurally parseable.

A second phase handles a late-arriving update: the model is asked to submit
only the *changed* fields plus new reasoning-log entries via
`submit_adaptation`, which are merged locally so the log is append-only and
the record change is auditable.
"""
import json
import os

from groq import Groq

from tools import TOOL_FUNCTIONS, reset_active_patient_context, set_active_patient_context
from validator import validate_record

# Available Groq models (check yours at console.groq.com/docs/models):
#   openai/gpt-oss-120b   <- best for tool calling (recommended)
#   llama3-70b-8192            <- older but very stable
#   mixtral-8x7b-32768         <- large context window
#   gemma2-9b-it               <- fast, lighter model
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_REVISIONS = 2

SYSTEM_PROMPT = """You are a clinical documentation orchestrator. You do NOT diagnose, \
prescribe, or make treatment decisions. Your job is to produce a verified, structured \
follow-up record and an action list for a human clinician to review.

You have tools to retrieve information: lookup_medication, lookup_allergy, get_labs, \
guideline_rag_search, get_prior_notes. Only call the tools relevant to what the \
transcript actually raises - do not call every tool by default, and do not guess at \
facts you could retrieve.

Process:
1. Plan (in your own reasoning) which specific facts need verification: new symptoms, \
medication changes, allergy claims, anything time-sensitive or contradicting prior notes.
2. Call tools to gather and reconcile those facts against the transcript.
3. For every mismatch between the transcript and retrieved records, classify it as either \
(a) transcript reveals newer info not yet in the records - adopt provisionally and flag for \
confirmation, or (b) a genuine contradiction you cannot resolve - escalate, do not guess, or \
(c) a clinically material combination worth flagging via guideline_rag_search - describe it \
as a monitoring/review flag, never as a diagnosis or instruction.
4. When ready, call the submit_final_record tool with the complete structured record. Every \
claim in subjective/objective/status_summary must be traceable to a source you retrieved or \
the transcript itself. Never use prescriptive language (e.g. 'start', 'prescribe', \
'discontinue', 'diagnose with') - use recommendation-for-review phrasing instead \
(e.g. 'recommend clinician review of X').
5. If told your submission failed validation, fix the specific issues listed and call \
submit_final_record again.
6. You must call at least one retrieval tool before submitting - never submit a record \
based only on the transcript.

IMPORTANT OUTPUT SHAPE RULES:
- record.objective should be a JSON object with descriptive keys and string values.
- If you draft objective as a single summary string, the caller will wrap it under the \
  key "summary"; however, prefer an object.
- Every unresolved_for_clinician_review item's source_conflict must be an array of strings.
- If no conflict is documented, use [] and NEVER null.
- Do not invent clinical facts or conflicts.
"""

# OpenAI/Groq-style function tool definitions. Same shapes as the Anthropic
# `input_schema` values from the previous version, just wrapped in the
# {"type": "function", "function": {...}} envelope Groq's API expects, with
# the field renamed to `parameters`.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_medication",
            "description": "Look up dosing and known interaction notes for a medication by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_allergy",
            "description": "Retrieve documented allergies on file for a patient.",
            "parameters": {
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_labs",
            "description": "Retrieve lab panel results for a patient, optionally filtered by panel name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "panel": {"type": "string"},
                },
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guideline_rag_search",
            "description": "Search the clinical guideline snippet corpus for monitoring/documentation guidance relevant to a query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_prior_notes",
            "description": "Retrieve prior visit notes for a patient.",
            "parameters": {
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_final_record",
            "description": "Submit the completed structured follow-up record. This ends the drafting phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record": {
                        "type": "object",
                        "properties": {
                            "subjective": {"type": "string"},
                            "objective": {"type": ["object", "string"]},
                            "status_summary": {"type": "string"},
                            "plan_actions": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["subjective", "objective", "status_summary", "plan_actions"],
                    },
                    "unresolved_for_clinician_review": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "issue": {"type": "string"},
                                "detail": {"type": "string"},
                                "source_conflict": {"type": ["array", "null"], "items": {"type": "string"}},
                            },
                            "required": ["issue", "detail"],
                        },
                    },
                    "reasoning_log": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "action": {"type": "string"},
                                "tool_called": {"type": ["string", "null"]},
                                "result": {"type": ["string", "null"]},
                                "decision": {"type": ["string", "null"]},
                            },
                            "required": ["step", "action"],
                        },
                    },
                },
                "required": ["record", "unresolved_for_clinician_review", "reasoning_log"],
            },
        },
    },
]

ADAPT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_adaptation",
        "description": "Submit an update to the already-finalized record in response to new information.",
        "parameters": {
            "type": "object",
            "properties": {
                "updated_record_fields": {
                    "type": "object",
                    "description": "Only the fields of `record` that changed (subjective/status_summary/plan_actions/objective).",
                },
                "updated_unresolved_for_clinician_review": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue": {"type": "string"},
                            "detail": {"type": "string"},
                            "source_conflict": {"type": ["array", "null"], "items": {"type": "string"}},
                        },
                        "required": ["issue", "detail"],
                    },
                    "description": "The full replacement list for unresolved_for_clinician_review.",
                },
                "new_reasoning_log_entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string"},
                            "action": {"type": "string"},
                            "tool_called": {"type": ["string", "null"]},
                            "result": {"type": ["string", "null"]},
                            "decision": {"type": ["string", "null"]},
                        },
                        "required": ["step", "action"],
                    },
                    "description": "New log entries ONLY - do not repeat prior entries.",
                },
            },
            "required": ["updated_record_fields", "new_reasoning_log_entries", "updated_unresolved_for_clinician_review"],
        },
    },
}


class Orchestrator:
    @staticmethod
    def _normalize_record_payload(payload: dict) -> dict:
        """Normalize harmless LLM output-shape variations before validation.

        This does not infer clinical facts: an objective string is preserved verbatim
        under `summary`, and null source_conflict values become empty lists.
        """
        if not isinstance(payload, dict):
            return payload

        record = payload.get("record")
        if isinstance(record, dict):
            objective = record.get("objective")
            if isinstance(objective, str):
                record["objective"] = {"summary": objective}
            elif objective is None:
                record["objective"] = {}

        unresolved = payload.get("unresolved_for_clinician_review", [])
        if isinstance(unresolved, list):
            for item in unresolved:
                if isinstance(item, dict) and item.get("source_conflict") is None:
                    item["source_conflict"] = []
        return payload

    @staticmethod
    def _normalize_adaptation_patch(patch: dict) -> dict:
        """Normalize output-shape variations in a late-update patch."""
        if not isinstance(patch, dict):
            return patch

        fields = patch.get("updated_record_fields", {})
        if isinstance(fields, dict):
            objective = fields.get("objective")
            if isinstance(objective, str):
                fields["objective"] = {"summary": objective}
            elif objective is None and "objective" in fields:
                fields["objective"] = {}

        unresolved = patch.get("updated_unresolved_for_clinician_review", [])
        if isinstance(unresolved, list):
            for item in unresolved:
                if isinstance(item, dict) and item.get("source_conflict") is None:
                    item["source_conflict"] = []
        return patch

    def __init__(self, patient_id: str, transcript: dict, api_key: str = None, event_callback=None, patient_context: dict = None):
        self.patient_id = patient_id
        self.transcript = transcript
        self.client = Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))
        self.event_callback = event_callback
        self.patient_context = patient_context
        self.messages = []
        self.final_record = None

    def _emit(self, event_type, message, tool=None, status=None):
        if self.event_callback:
            self.event_callback({
                "type": event_type,
                "message": message,
                "tool": tool,
                "status": status,
            })

    def _transcript_text(self) -> str:
        lines = [f"{t['speaker']}: {t['text']}" for t in self.transcript["turns"]]
        return "\n".join(lines)

    def _run_tool(self, name, tool_input):
        fn = TOOL_FUNCTIONS.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        return fn(**tool_input)

    def _call_model(self, tools):
        self._emit("agent", "Agent is deciding which information needs verification.")
        response = self.client.chat.completions.create(
            model=MODEL,
            max_tokens=3000,
            temperature=0.2,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + self.messages,
            tools=tools,
            tool_choice="auto",
        )
        return response.choices[0].message

    @staticmethod
    def _message_to_dict(message) -> dict:
        """Turn a Groq SDK message object into a plain dict suitable for
        re-sending as conversation history."""
        d = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        return d

    @staticmethod
    def _parse_arguments(tool_call):
        """Tool call arguments arrive as a JSON string; guard against a
        model producing malformed JSON instead of crashing the run."""
        try:
            return json.loads(tool_call.function.arguments), None
        except (json.JSONDecodeError, TypeError):
            return None, f"Arguments for {tool_call.function.name} were not valid JSON."

    def draft(self) -> dict:
        """Phase 1: plan -> gather -> reconcile -> draft -> validate -> revise."""
        token = set_active_patient_context(self.patient_context) if self.patient_context else None
        try:
            self.messages = [{
                "role": "user",
                "content": (
                    f"Patient ID: {self.patient_id}\n"
                    f"Visit date: {self.transcript.get('visit_date', '2026-10-25')}\n\n"
                    f"Consultation transcript:\n{self._transcript_text()}\n\n"
                    "Produce a verified structured follow-up record. Retrieve whatever "
                    "supporting records you need before drafting."
                ),
            }]

            revisions = 0
            tools = TOOLS
            while True:
                message = self._call_model(tools)
                self.messages.append(self._message_to_dict(message))

                tool_calls = message.tool_calls or []
                submit = next((tc for tc in tool_calls if tc.function.name == "submit_final_record"), None)

                if submit is not None:
                    self._emit("submit", "Structured draft submitted to safety and structure validation.", tool="submit_final_record")
                    candidate, err = self._parse_arguments(submit)
                    if err:
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": submit.id,
                            "name": submit.function.name,
                            "content": json.dumps({"status": "FAIL", "issues": [err]}),
                        })
                        continue

                    candidate = self._normalize_record_payload(candidate)
                    result = validate_record(candidate)
                    self._emit("validation", f"Validator returned {result['status']}." if result['status'] == "PASS" else f"Validator rejected the draft with {len(result['issues'])} issue(s).", status=result["status"])
                    if result["status"] == "PASS" or revisions >= MAX_REVISIONS:
                        self.final_record = candidate
                        self.final_record["_validation"] = result
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": submit.id,
                            "name": submit.function.name,
                            "content": json.dumps(result),
                        })
                        return self.final_record
                    else:
                        revisions += 1
                        self._emit("revision", f"Revision {revisions}: fixing validator findings and resubmitting.", status="REVISION")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": submit.id,
                            "name": submit.function.name,
                            "content": json.dumps({
                                "status": "FAIL",
                                "issues": result["issues"],
                                "instruction": "Fix these specific issues and call submit_final_record again.",
                            }),
                        })
                        continue

                if tool_calls:
                    for tc in tool_calls:
                        args, err = self._parse_arguments(tc)
                        self._emit("tool", f"Calling {tc.function.name}.", tool=tc.function.name)
                        output = {"error": err} if err else self._run_tool(tc.function.name, args)
                        self._emit("tool_result", f"{tc.function.name} returned supporting data.", tool=tc.function.name, status="OK" if not err else "ERROR")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": json.dumps(output),
                        })
                    continue

                # Model returned plain text with no tool call and no submission yet - nudge it.
                self.messages.append({
                    "role": "user",
                    "content": "Please continue by calling tools as needed, then call submit_final_record.",
                })
        finally:
            reset_active_patient_context(token)

    def adapt(self, late_update_text: str) -> dict:
        """Phase 2: incorporate a late-arriving update, append-only to the log."""
        if self.final_record is None:
            raise RuntimeError("draft() must run before adapt()")

        token = set_active_patient_context(self.patient_context) if self.patient_context else None
        try:
            self.messages.append({
                "role": "user",
                "content": (
                    f"New information has arrived after the record was finalized:\n"
                    f"\"{late_update_text}\"\n\n"
                    "Re-check only the fields this affects. Call submit_adaptation with the "
                    "changed fields, the full updated unresolved-for-review list, and NEW "
                    "reasoning log entries only (do not repeat earlier entries)."
                ),
            })

            while True:
                message = self._call_model([ADAPT_TOOL] + TOOLS)
                self.messages.append(self._message_to_dict(message))
                tool_calls = message.tool_calls or []
                submit = next((tc for tc in tool_calls if tc.function.name == "submit_adaptation"), None)

                if submit is not None:
                    self._emit("adapt", "Late update submitted for targeted record adaptation.", tool="submit_adaptation")
                    patch, err = self._parse_arguments(submit)
                    if err:
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": submit.id,
                            "name": submit.function.name,
                            "content": json.dumps({"status": "FAIL", "issues": [err]}),
                        })
                        continue

                    patch = self._normalize_adaptation_patch(patch)
                    self.final_record["record"].update(patch.get("updated_record_fields", {}))
                    self.final_record["unresolved_for_clinician_review"] = patch.get(
                        "updated_unresolved_for_clinician_review",
                        self.final_record["unresolved_for_clinician_review"],
                    )
                    self.final_record["reasoning_log"].extend(patch.get("new_reasoning_log_entries", []))
                    result = validate_record(self.final_record)
                    self._emit("validation", f"Adapted record validation: {result['status']}.", status=result["status"])
                    self.final_record["_validation"] = result
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": submit.id,
                        "name": submit.function.name,
                        "content": json.dumps(result),
                    })
                    return self.final_record

                if tool_calls:
                    for tc in tool_calls:
                        args, err = self._parse_arguments(tc)
                        self._emit("tool", f"Calling {tc.function.name}.", tool=tc.function.name)
                        output = {"error": err} if err else self._run_tool(tc.function.name, args)
                        self._emit("tool_result", f"{tc.function.name} returned supporting data.", tool=tc.function.name, status="OK" if not err else "ERROR")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": json.dumps(output),
                        })
                    continue

                self.messages.append({
                    "role": "user",
                    "content": "Please call submit_adaptation with the update.",
                })
        finally:
            reset_active_patient_context(token)

