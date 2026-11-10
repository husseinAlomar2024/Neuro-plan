"""Tool definitions (JSON Schema) and argument validation. One schema for GUI, LLM and offline parser."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .. import config


class ToolError(Exception):
    """A tool call that cannot run: bad arguments or an impossible request. Message is user-facing."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...] = ()
    write: bool = True
    regenerates: bool = False
    requires_confirmation: bool = False
    llm_visible: bool = True

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": self.properties, "required": list(self.required),
                "additionalProperties": False}


_SUBJECT = {"type": ["string", "integer"], "description": "Subject name (or id)."}
_DATE = {"type": "string", "format": "date", "description": "ISO date YYYY-MM-DD ('today'/'tomorrow' accepted)."}
_TIME = {"type": "string", "format": "time", "description": "HH:MM, 24h."}
_MINUTES_DAY = {"type": "integer", "minimum": 0, "maximum": config.MAX_DAY_MINUTES}
_ID = {"type": "integer", "minimum": 1}
_CONFIRMED = {"type": "boolean", "description": "Set true only after the student confirmed."}
_ASSESSMENT_FIELDS: dict[str, dict[str, Any]] = {
    "kind": {"type": "string", "enum": list(config.ASSESSMENT_KINDS)},
    "title": {"type": "string", "minLength": 1, "maxLength": 80},
    "date": _DATE,
    "weight_pct": {"type": "integer", "minimum": 0, "maximum": 100},
    "unit_from": {"type": "integer", "minimum": 1},
    "unit_to": {"type": "integer", "minimum": 1},
}

_SPECS: list[ToolSpec] = [
    # ------------------------------------------------------------ read tools
    ToolSpec("get_state", "Compact snapshot: subjects with backlog and next assessment, availability, "
             "today's blocks, due reviews, risks, streak, missing info.", {}, write=False),
    ToolSpec("get_plan", "Planned study blocks per day between two dates (default: today only).",
             {"date_from": _DATE, "date_to": _DATE}, write=False),
    ToolSpec("explain_priority", "Real priority numbers (W factor breakdown, minutes per day) for one "
             "subject or all. Call before quoting figures.", {"subject": _SUBJECT}, write=False),
    ToolSpec("get_risks", "Assessments the current plan cannot fully prepare for, each with three "
             "computed options.", {}, write=False),
    # ------------------------------------------------------------ write tools
    ToolSpec("upsert_subject", "Create a subject or update the one with this exact name. "
             "total_units creates units 1..N.",
             {"name": {"type": "string", "minLength": 1, "maxLength": 60},
              "new_name": {"type": "string", "minLength": 1, "maxLength": 60},
              "category": {"type": "string", "enum": list(config.CATEGORIES),
                           "description": "HEAVY=math/theory, CODING=practical, MEMORY=language/reading"},
              "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
              "mastery": {"type": "integer", "minimum": 1, "maximum": 5},
              "minutes_per_unit": {"type": "integer", "minimum": 5, "maximum": 600},
              "total_units": {"type": "integer", "minimum": 0, "maximum": 200},
              "expected_units_by_now": {"type": "integer", "minimum": 0, "maximum": 200},
              "done_units": {"type": "integer", "minimum": 0, "maximum": 200},
              "color_hex": {"type": "string", "pattern": r"^#[0-9A-Fa-f]{6}$"},
              "active": {"type": "boolean"}},
             ("name",), regenerates=True),
    ToolSpec("delete_subject", "Delete a subject with its units, assessments, reviews and blocks.",
             {"subject": _SUBJECT, "confirmed": _CONFIRMED}, ("subject",), regenerates=True,
             requires_confirmation=True),
    ToolSpec("set_progress", "Set how many units the student should have covered by now and/or how "
             "many are actually done (units 1..N).",
             {"subject": _SUBJECT,
              "expected_units_by_now": {"type": "integer", "minimum": 0, "maximum": 200},
              "done_units": {"type": "integer", "minimum": 0, "maximum": 200}},
             ("subject",), regenerates=True),
    ToolSpec("log_study", "Log study done outside the timer: either minutes studied or whole units finished.",
             {"subject": _SUBJECT,
              "minutes": {"type": "integer", "minimum": 1, "maximum": 600},
              "units": {"type": "integer", "minimum": 1, "maximum": 50}},
             ("subject",), regenerates=True),
    ToolSpec("add_assessment", "Add a quiz/midterm/final/assignment/project for a subject.",
             {"subject": _SUBJECT, **_ASSESSMENT_FIELDS}, ("subject", "kind", "date"), regenerates=True),
    ToolSpec("update_assessment", "Change fields of an assessment by id.",
             {"id": _ID, **_ASSESSMENT_FIELDS}, ("id",), regenerates=True),
    ToolSpec("delete_assessment", "Delete an assessment by id.", {"id": _ID}, ("id",), regenerates=True),
    ToolSpec("set_weekly_availability", "Set study minutes / start time / peak window for weekdays.",
             {"days": {"type": "array", "minItems": 1, "items": {
                 "type": "object",
                 "properties": {
                     "weekday": {"type": ["integer", "string"],
                                 "description": "0=Mon..6=Sun or mon/tue/wed/thu/fri/sat/sun"},
                     "minutes": _MINUTES_DAY, "start_time": _TIME,
                     "peak_window": {"type": "string", "enum": list(config.PEAK_WINDOWS)}},
                 "required": ["weekday"], "additionalProperties": False}}},
             ("days",), regenerates=True),
    ToolSpec("set_day_override", "Set the available minutes for one specific date (clear=true removes it).",
             {"date": _DATE, "minutes": _MINUTES_DAY, "start_time": _TIME, "clear": {"type": "boolean"}},
             ("date",), regenerates=True),
    ToolSpec("complete_block", "Mark a study block done (or partial with the minutes actually studied).",
             {"id": _ID, "actual_min": {"type": "integer", "minimum": 0, "maximum": 600},
              "status": {"type": "string", "enum": ["done", "partial"]},
              "rating": {"type": "string", "enum": list(config.RATINGS),
                         "description": "Only for REVIEW blocks; default good."}},
             ("id",), regenerates=True),
    ToolSpec("skip_block", "Mark a pending block as skipped; its work is redistributed.",
             {"id": _ID, "reason": {"type": "string", "maxLength": 200}}, ("id",), regenerates=True),
    ToolSpec("lock_block", "Lock (or unlock) a block so regeneration keeps it.",
             {"id": _ID, "locked": {"type": "boolean"}}, ("id",)),
    ToolSpec("move_block", "Move a pending block to another date (it becomes locked there).",
             {"id": _ID, "new_date": _DATE}, ("id", "new_date"), regenerates=True),
    ToolSpec("complete_review", "Rate a spaced-repetition review: again, hard, good or easy.",
             {"id": _ID, "rating": {"type": "string", "enum": list(config.RATINGS)}},
             ("id", "rating"), regenerates=True),
    ToolSpec("regenerate_plan", "Rebuild the plan from today with the current data.", {}, regenerates=True),
    ToolSpec("undo_last", "Undo the most recent change.", {}),
    ToolSpec("set_cap_boost", "Raise (or reset) one subject's daily cap to the exam cap.",
             {"subject": _SUBJECT, "enabled": {"type": "boolean"}}, ("subject", "enabled"), regenerates=True),
    ToolSpec("dismiss_missing_info", "Stop asking for a piece of onboarding info the student does not "
             "have (e.g. no assessment scheduled yet).",
             {"subject": _SUBJECT, "field": {"type": "string", "enum": ["units", "expected", "assessment"]}},
             ("subject", "field")),
    # ------------------------------------------------------------ UI-only tools
    ToolSpec("list_subjects", "All subjects with units and assessments.", {}, write=False, llm_visible=False),
    ToolSpec("list_reviews", "Due and upcoming reviews.", {}, write=False, llm_visible=False),
    ToolSpec("get_availability", "Weekly availability and overrides.", {}, write=False, llm_visible=False),
    ToolSpec("get_settings", "Editable settings.", {}, write=False, llm_visible=False),
    ToolSpec("export_data", "Everything as a JSON-serialisable dict.", {}, write=False, llm_visible=False),
    ToolSpec("update_settings", "Change app settings.",
             {"settings": {"type": "object"}}, ("settings",), llm_visible=False),
    ToolSpec("reset_demo_data", "Replace all data with the demo data set.",
             {"confirmed": _CONFIRMED}, regenerates=True, requires_confirmation=True, llm_visible=False),
    ToolSpec("import_data", "Replace all data with an exported JSON document.",
             {"data": {"type": "object"}, "confirmed": _CONFIRMED}, ("data",), regenerates=True,
             requires_confirmation=True, llm_visible=False),
]

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def llm_specs() -> list[ToolSpec]:
    return [s for s in _SPECS if s.llm_visible]


def export_schema(schema: Any) -> Any:
    """The schema as sent to LLM providers: the same shape, minus what some servers reject
    (custom ``format`` keywords, union types). Validation here still accepts the richer forms."""
    if isinstance(schema, list):
        return [export_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "format":
            continue
        if key == "type" and isinstance(value, list):
            out[key] = "string" if "string" in value else value[0]
        else:
            out[key] = export_schema(value)
    return out


def anthropic_tools() -> list[dict[str, Any]]:
    return [{"name": s.name, "description": s.description, "input_schema": export_schema(s.schema)}
            for s in llm_specs()]


def openai_tools() -> list[dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": s.name, "description": s.description, "parameters": export_schema(s.schema)}}
            for s in llm_specs()]


# ---------------------------------------------------------------- validation
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_TYPE_NAMES = {"string": "text", "integer": "a whole number", "number": "a number", "boolean": "true/false",
               "array": "a list", "object": "an object"}


def _coerce(value: Any, type_name: str) -> tuple[bool, Any]:
    if type_name == "string":
        return (True, value) if isinstance(value, str) else (False, value)
    if type_name == "integer":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, int):
            return True, value
        if isinstance(value, float) and value.is_integer():
            return True, int(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return True, int(value.strip())
        return False, value
    if type_name == "number":
        if isinstance(value, bool):
            return False, value
        if isinstance(value, (int, float)):
            return True, value
        return False, value
    if type_name == "boolean":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return True, value.lower() == "true"
        return False, value
    if type_name == "array":
        return isinstance(value, list), value
    if type_name == "object":
        return isinstance(value, dict), value
    return True, value


def _validate_value(path: str, value: Any, schema: dict[str, Any]) -> Any:
    types = schema.get("type")
    if types:
        type_list = types if isinstance(types, list) else [types]
        # prefer an exact type match so "3" stays a string when strings are allowed
        ordered = sorted(type_list, key=lambda t: 0 if _is_exact(value, t) else 1)
        for type_name in ordered:
            ok, coerced = _coerce(value, type_name)
            if ok:
                value = coerced
                break
        else:
            wanted = " or ".join(_TYPE_NAMES.get(t, t) for t in type_list)
            raise ToolError(f"'{path}' must be {wanted} (got {value!r})")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"'{path}' must be one of {', '.join(map(str, schema['enum']))} (got {value!r})")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            if lo is not None and hi is not None:
                raise ToolError(f"'{path}' must be between {lo} and {hi} (got {value})")
            bound = f"at least {lo}" if lo is not None else f"at most {hi}"
            raise ToolError(f"'{path}' must be {bound} (got {value})")
    if isinstance(value, str):
        if "minLength" in schema and len(value.strip()) < schema["minLength"]:
            raise ToolError(f"'{path}' must not be empty")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolError(f"'{path}' is too long (max {schema['maxLength']} characters)")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ToolError(f"'{path}' has an invalid format (got {value!r})")
        fmt = schema.get("format")
        if fmt == "date" and value.strip().lower() not in ("today", "tomorrow"):
            try:
                date.fromisoformat(value.strip())
            except ValueError:
                raise ToolError(f"'{path}' must be a date like 2026-11-24 (got {value!r})") from None
        if fmt == "time":
            if not _TIME_RE.match(value.strip()):
                raise ToolError(f"'{path}' must be a time like 18:00 (got {value!r})")
            h, m = value.strip().split(":")
            value = f"{int(h):02d}:{m}"
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ToolError(f"'{path}' must contain at least {schema['minItems']} item(s)")
        if "items" in schema:
            value = [_validate_value(f"{path}[{i}]", item, schema["items"]) for i, item in enumerate(value)]
    if isinstance(value, dict) and "properties" in schema:
        value = _validate_object(path + ".", value, schema)
    return value


def _is_exact(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    return False


def _validate_object(prefix: str, args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, dict[str, Any]] = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args or args[key] is None:
            raise ToolError(f"'{prefix}{key}' is required")
    clean: dict[str, Any] = {}
    for key, value in args.items():
        if key not in props:
            if schema.get("additionalProperties", True) is False:
                known = ", ".join(props) or "none"
                raise ToolError(f"Unknown argument '{prefix}{key}' (allowed: {known})")
            clean[key] = value
            continue
        if value is None:
            continue
        clean[key] = _validate_value(prefix + key, value, props[key])
    return clean


def validate_args(spec: ToolSpec, args: Any) -> dict[str, Any]:
    """Validate and normalise tool arguments. Raises ToolError with a clear, user-facing message."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError(f"{spec.name}: arguments must be an object")
    try:
        return _validate_object("", args, spec.schema)
    except ToolError as exc:
        raise ToolError(f"{spec.name}: {exc}") from None
