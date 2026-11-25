"""ToolExecutor: validates arguments, runs each tool in a DB transaction, logs an undoable action,
regenerates the plan when needed and emits events. GUI, LLM and offline parser all come through here."""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from datetime import date, timedelta
from typing import Any, Callable, Optional

from .. import config, events
from ..db.repository import Repository
from ..events import EventBus
from ..models import Assessment, PlanResult, Review, Risk, StudyBlock, Subject, ToolResult, Unit
from ..scheduler.engine import review_units
from ..scheduler.service import cap_boost_ids, compute_plan, horizon_days, regenerate
from .matching import match_subject
from .registry import TOOLS, ToolError, ToolSpec, validate_args

log = logging.getLogger(__name__)
SOURCES = ("ui", "chat_llm", "chat_offline", "system")
_WEEKDAY_LOOKUP = {name: i for i, name in enumerate(config.WEEKDAY_NAMES)}
_WEEKDAY_LOOKUP.update({name.lower(): i for i, name in enumerate(config.WEEKDAY_LONG)})
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=]*$")
ONBOARDING_FIELDS = ("units", "expected", "assessment")


def next_review_state(stage: int, rating: str, today: date) -> tuple[int, Optional[date]]:
    """SPEC 4.5: (new stage, new due date); due date None means the review cycle is finished."""
    stages = config.REVIEW_STAGES_DAYS
    stage = max(0, min(stage, len(stages) - 1))
    if rating == "again":
        return 0, today + timedelta(days=stages[0])
    if rating == "hard":
        return stage, today + timedelta(days=math.ceil(stages[stage] / 2))
    new_stage = stage + (1 if rating == "good" else 2)
    if new_stage >= len(stages):
        return len(stages) - 1, None
    return new_stage, today + timedelta(days=stages[new_stage])


def describe_diff(diff: list[dict[str, Any]], today: date, max_days: int = 2, per_day: int = 3) -> str:
    """Human summary of a plan diff: 'Today: Deep Learning +50 min, IELTS Prep −25 min; Tomorrow: …'."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for entry in diff:
        by_date.setdefault(entry["date"], []).append(entry)
    parts: list[str] = []
    for iso in sorted(by_date)[:max_days]:
        d = date.fromisoformat(iso)
        label = "Today" if d == today else "Tomorrow" if d == today + timedelta(days=1) else d.strftime("%a %d %b")
        items = sorted(by_date[iso], key=lambda e: -abs(e["delta_min"]))[:per_day]
        text = ", ".join(f"{e['subject']} {'+' if e['delta_min'] > 0 else '−'}{abs(e['delta_min'])} min"
                         for e in items)
        parts.append(f"{label}: {text}")
    more = len(by_date) - max_days
    if more > 0:
        parts.append(f"{more} more day(s) adjusted")
    return "; ".join(parts)


class ToolExecutor:
    def __init__(self, repo: Repository, bus: Optional[EventBus] = None,
                 today_fn: Callable[[], date] = date.today) -> None:
        self.repo = repo
        self.bus = bus or EventBus()
        self.today = today_fn
        self._lock = threading.RLock()
        self._plan: Optional[PlanResult] = None
        self._plan_day: Optional[date] = None
        self._known_risks: set[int] = set()
        self._pending_events: list[tuple[str, dict[str, Any]]] = []
        self._force_regen = False

    # ------------------------------------------------------------ public API
    def execute(self, tool_name: str, args: Optional[dict[str, Any]] = None, source: str = "ui") -> ToolResult:
        spec = TOOLS.get(tool_name)
        if spec is None:
            return ToolResult(False, f"Unknown tool '{tool_name}'", tool=tool_name)
        if source not in SOURCES:
            return ToolResult(False, f"Unknown source '{source}'", tool=tool_name)
        try:
            clean = validate_args(spec, args)
            if not spec.write:
                with self._lock:
                    summary, data = self._handler(spec)(clean)
                return ToolResult(True, summary, data, tool=tool_name)
            if spec.requires_confirmation and not clean.get("confirmed"):
                with self._lock:
                    preview = self._preview(tool_name, clean)
                return ToolResult(True, preview, {"tool": tool_name, "args": {**clean, "confirmed": True}},
                                  needs_confirmation=True, preview=preview, tool=tool_name)
            return self._run_write(spec, clean, source)
        except ToolError as exc:
            return ToolResult(False, str(exc), tool=tool_name)
        except Exception as exc:  # rolled back by the transaction; surface it in the coach panel
            log.exception("tool %s failed", tool_name)
            message = f"Something went wrong in {tool_name}: {exc}. Nothing was changed."
            self.bus.emit(events.ERROR, message=message)
            return ToolResult(False, message, tool=tool_name)

    def plan(self) -> PlanResult:
        """Latest plan result (risks, explanations); recomputed lazily without touching the DB."""
        with self._lock:
            today = self.today()
            if self._plan is None or self._plan_day != today:
                self._plan = compute_plan(self.repo, today)
                self._plan_day = today
            return self._plan

    def missing_info(self) -> list[dict[str, Any]]:
        """What onboarding still has to ask: [{'subject': name|None, 'field': ...}], most impactful first."""
        subjects = self.repo.list_subjects(active_only=True)
        if not subjects:
            return [{"subject": None, "field": "subjects"}]
        dismissed = self._dismissed()
        missing: list[dict[str, Any]] = []
        for s in subjects:
            units = self.repo.list_units(s.id)
            done = sum(1 for u in units if u.status == "done")
            if not units and f"{s.id}:units" not in dismissed:
                missing.append({"subject": s.name, "field": "units"})
            if (s.expected_units_by_now == 0 and done == 0 and f"{s.id}:expected" not in dismissed):
                missing.append({"subject": s.name, "field": "expected"})
            if not self.repo.list_assessments(s.id) and f"{s.id}:assessment" not in dismissed:
                missing.append({"subject": s.name, "field": "assessment"})
        order = {"units": 0, "assessment": 1, "expected": 2}
        missing.sort(key=lambda m: order[m["field"]])
        if all(av.minutes == 0 for av in self.repo.get_availability().values()):
            missing.insert(0, {"subject": None, "field": "availability"})
        return missing

    def streak_days(self) -> int:
        today = self.today()
        days = self.repo.study_dates()
        d = today if today in days else today - timedelta(days=1)
        streak = 0
        while d in days:
            streak += 1
            d -= timedelta(days=1)
        return streak

    # ------------------------------------------------------------ write pipeline
    def _handler(self, spec: ToolSpec) -> Callable[[dict[str, Any]], tuple[str, Any]]:
        return getattr(self, "_t_" + spec.name)  # type: ignore[no-any-return]

    def _run_write(self, spec: ToolSpec, args: dict[str, Any], source: str) -> ToolResult:
        with self._lock:
            self._pending_events = []
            self._force_regen = False
            today = self.today()
            diff: list[dict[str, Any]] = []
            with self.repo.transaction():
                is_undo = spec.name == "undo_last"
                inverse: Optional[dict[str, Any]] = None if is_undo else {"snapshot": self.repo.snapshot()}
                names = {s.id: s.name for s in self.repo.list_subjects()}
                before = self._plan_minutes(today)
                summary, data = self._handler(spec)(args)
                if isinstance(data, dict) and "_settings_prev" in data and inverse is not None:
                    inverse["settings_prev"] = data.pop("_settings_prev")
                regenerated = spec.regenerates or self._force_regen
                if regenerated:
                    self._plan = regenerate(self.repo, today)
                    self._plan_day = today
                else:
                    self._plan = None
                names.update({s.id: s.name for s in self.repo.list_subjects()})
                left_today = sum(1 for b in self.repo.list_blocks(today, today) if b.status == "pending")
                for name, payload in self._pending_events:
                    if name == events.BLOCK_FINISHED:
                        payload["last_of_day"] = left_today == 0
                        payload["pending_today"] = left_today
                after = self._plan_minutes(today)
                for key in sorted(set(before) | set(after)):
                    delta = after.get(key, 0) - before.get(key, 0)
                    if delta:
                        diff.append({"date": key[0], "subject": names.get(key[1], "?"), "delta_min": delta})
                logged_args = {k: v for k, v in args.items() if k != "data"}
                action_id = self.repo.add_action(source, spec.name, logged_args, inverse)
            pending = self._pending_events
            self._pending_events = []
        # ---- after commit: events
        self.bus.emit(events.STATE_CHANGED, tool=spec.name, source=source)
        for name, payload in pending:
            self.bus.emit(name, source=source, **payload)
        if diff and (regenerated or spec.name == "undo_last"):
            self.bus.emit(events.PLAN_CHANGED, diff=diff, tool=spec.name, source=source,
                          summary=describe_diff(diff, today))
        self._detect_risks(source)
        return ToolResult(True, summary, data, action_id=action_id, tool=spec.name,
                          plan_change=describe_diff(diff, today) if diff else "")

    def _plan_minutes(self, today: date) -> dict[tuple[str, int], int]:
        out: dict[tuple[str, int], int] = {}
        for b in self.repo.list_blocks(today, today + timedelta(days=6)):
            if b.status != "skipped":
                key = (b.plan_date.isoformat(), b.subject_id)
                out[key] = out.get(key, 0) + b.duration_min
        return out

    def _detect_risks(self, source: str) -> None:
        risks = self.plan().risks
        current = {r.assessment_id for r in risks}
        new = [r for r in risks if r.assessment_id not in self._known_risks]
        self._known_risks = current
        if new:
            self.bus.emit(events.RISK_DETECTED, risks=[self._risk_dict(r) for r in new], source=source)

    def _queue(self, name: str, **payload: Any) -> None:
        self._pending_events.append((name, payload))

    def _preview(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "delete_subject":
            s = self._subject(args["subject"])
            units = len(self.repo.list_units(s.id))
            assessments = len(self.repo.list_assessments(s.id))
            return (f"Delete '{s.name}' with its {units} unit(s) and {assessments} assessment(s), and "
                    f"re-plan without it?")
        if tool == "reset_demo_data":
            return "Replace ALL current data (subjects, plan, chat) with the demo data set?"
        if tool == "import_data":
            if args["data"].get("format") != "neuroplan-export-1":
                raise ToolError("import_data: this file is not a NeuroPlan export")
            return "Replace ALL current data with the imported file?"
        return f"Run {tool}?"

    # ------------------------------------------------------------ helpers
    def _dismissed(self) -> set[str]:
        try:
            return set(json.loads(self.repo.get_setting("onboarding_skipped") or "[]"))
        except ValueError:
            return set()

    def _subject(self, ref: Any) -> Subject:
        subjects = self.repo.list_subjects()
        if isinstance(ref, int):
            for s in subjects:
                if s.id == ref:
                    return s
            raise ToolError(f"No subject with id {ref}")
        name = match_subject(str(ref), [s.name for s in subjects])
        if name is None:
            known = ", ".join(s.name for s in subjects) or "none yet"
            raise ToolError(f"Unknown subject '{ref}'. Known subjects: {known}")
        return next(s for s in subjects if s.name == name)

    def _date(self, value: str) -> date:
        v = value.strip().lower()
        if v == "today":
            return self.today()
        if v == "tomorrow":
            return self.today() + timedelta(days=1)
        return date.fromisoformat(v)

    def _block(self, block_id: int) -> StudyBlock:
        block = self.repo.get_block(block_id)
        if block is None:
            raise ToolError(f"No study block with id {block_id}")
        return block

    def _pending_block(self, block_id: int) -> StudyBlock:
        block = self._block(block_id)
        if block.status != "pending":
            raise ToolError(f"Block {block_id} is already {block.status}")
        return block

    def _mark_unit_done(self, subject: Subject, unit: Unit, today: date) -> None:
        self.repo.update_unit(unit.id, status="done", minutes_logged=subject.minutes_per_unit, done_date=today)
        self.repo.delete_reviews_for_unit(subject.id, unit.number)
        self.repo.insert_review(subject.id, unit.number, 0, today + timedelta(days=config.REVIEW_STAGES_DAYS[0]))

    def _apply_minutes(self, subject: Subject, minutes: int, start_unit: Optional[int] = None) -> list[int]:
        """Add minutes to the next undone unit; overflow flows to the following units."""
        today = self.today()
        undone = [u for u in self.repo.list_units(subject.id) if u.status != "done"]
        if start_unit is not None and any(u.number == start_unit for u in undone):
            undone = [u for u in undone if u.number >= start_unit] + [u for u in undone if u.number < start_unit]
        finished: list[int] = []
        left = minutes
        for unit in undone:
            if left <= 0:
                break
            take = min(left, max(0, subject.minutes_per_unit - unit.minutes_logged))
            left -= take
            if unit.minutes_logged + take >= subject.minutes_per_unit:
                self._mark_unit_done(subject, unit, today)
                finished.append(unit.number)
            elif take > 0:
                self.repo.update_unit(unit.id, minutes_logged=unit.minutes_logged + take, status="in_progress")
        return finished

    def _set_done_units(self, subject: Subject, done_units: int) -> None:
        """Make exactly units 1..done_units done."""
        today = self.today()
        for unit in self.repo.list_units(subject.id):
            if unit.number <= done_units and unit.status != "done":
                self._mark_unit_done(subject, unit, today)
            elif unit.number > done_units and unit.status == "done":
                self.repo.update_unit(unit.id, status="todo", minutes_logged=0, done_date=None)
                self.repo.delete_reviews_for_unit(subject.id, unit.number)

    def _ensure_units(self, subject: Subject, at_least: int) -> None:
        if at_least > len(self.repo.list_units(subject.id)):
            self.repo.set_total_units(subject.id, at_least)

    def _apply_review_rating(self, review: Review, rating: str) -> str:
        today = self.today()
        stage, due = next_review_state(review.stage_index, rating, today)
        if due is None:
            self.repo.update_review(review.id, stage_index=stage, last_rating=rating, done=True)
            return "review cycle complete"
        self.repo.update_review(review.id, stage_index=stage, due_date=due, last_rating=rating)
        return f"next review {due.isoformat()}"

    # ------------------------------------------------------------ dict builders
    def _block_dict(self, b: StudyBlock, subjects: dict[int, Subject]) -> dict[str, Any]:
        s = subjects.get(b.subject_id)
        return {"id": b.id, "date": b.plan_date.isoformat(), "start": b.start_time,
                "subject": s.name if s else "?", "subject_id": b.subject_id,
                "color": s.color_hex if s else "#888888", "category": s.category if s else "",
                "unit": b.unit_number, "type": b.block_type, "pomodoro": b.pomodoro,
                "min": b.duration_min, "task": b.task_text, "status": b.status,
                "actual_min": b.actual_min, "locked": b.locked}

    @staticmethod
    def _assessment_dict(a: Assessment, today: date) -> dict[str, Any]:
        return {"id": a.id, "kind": a.kind, "title": a.title, "date": a.date.isoformat(),
                "days_until": (a.date - today).days, "weight_pct": a.weight_pct,
                "unit_from": a.unit_from, "unit_to": a.unit_to}

    @staticmethod
    def _risk_dict(r: Risk) -> dict[str, Any]:
        return {"assessment_id": r.assessment_id, "subject": r.subject_name, "title": r.title,
                "date": r.date.isoformat(), "needed_min": r.needed_min, "available_min": r.available_min,
                "shortfall_min": r.shortfall_min,
                "options": [{"kind": o.kind, "label": o.label, "detail": o.detail, "actions": o.actions,
                             "data": o.data} for o in r.options]}

    def _capacity(self, d: date) -> int:
        override = self.repo.get_overrides(d).get(d)
        return override.minutes if override else self.repo.get_availability()[d.weekday()].minutes

    # ------------------------------------------------------------ read tools
    def _t_get_state(self, a: dict[str, Any]) -> tuple[str, Any]:
        today = self.today()
        subjects = self.repo.list_subjects()
        by_id = {s.id: s for s in subjects}
        boosts = cap_boost_ids(self.repo)
        out_subjects: list[dict[str, Any]] = []
        backlog_total = 0
        for s in subjects:
            units = self.repo.list_units(s.id)
            done = sum(1 for u in units if u.status == "done")
            remaining = sum(max(0, s.minutes_per_unit - u.minutes_logged) for u in units if u.status != "done")
            upcoming = [x for x in self.repo.list_assessments(s.id) if x.date >= today]
            backlog = max(0, s.expected_units_by_now - done)
            if s.active:
                backlog_total += backlog
            entry: dict[str, Any] = {
                "name": s.name, "category": s.category, "difficulty": s.difficulty, "mastery": s.mastery,
                "units_total": len(units), "units_done": done, "expected_by_now": s.expected_units_by_now,
                "backlog_units": backlog, "remaining_min": remaining, "min_per_unit": s.minutes_per_unit,
                "next_assessment": ({k: v for k, v in self._assessment_dict(upcoming[0], today).items()
                                     if k != "weight_pct"} if upcoming else None)}
            if not s.active:
                entry["active"] = False
            if s.id in boosts:
                entry["cap_boost"] = True
            out_subjects.append(entry)
        availability = self.repo.get_availability()
        today_blocks = self.repo.list_blocks(today, today)
        reviews_due = sum(1 for r in self.repo.list_reviews() if r.due_date <= today and r.subject_id in by_id)
        planned = sum(b.duration_min for b in today_blocks if b.status != "skipped")
        done_min = sum((b.actual_min if b.actual_min is not None else b.duration_min)
                       for b in today_blocks if b.status in ("done", "partial"))
        data = {
            "today": today.isoformat(), "weekday": config.WEEKDAY_LONG[today.weekday()],
            "subjects": out_subjects,
            "availability": {config.WEEKDAY_NAMES[wd]: {"min": av.minutes, "start": av.start_time,
                                                         "peak": av.peak_window}
                             for wd, av in sorted(availability.items())},
            "overrides": [{"date": o.date.isoformat(), "min": o.minutes}
                          for o in sorted(self.repo.get_overrides(today).values(), key=lambda o: o.date)],
            "today_capacity_min": self._capacity(today),
            "today_blocks": [{"id": b.id, "start": b.start_time, "subject": by_id[b.subject_id].name,
                              "unit": b.unit_number, "type": b.block_type, "min": b.duration_min,
                              "status": b.status, **({"locked": True} if b.locked else {})}
                             for b in today_blocks if b.subject_id in by_id],
            "kpis": {"backlog_units_total": backlog_total, "planned_min_today": planned,
                     "done_min_today": done_min, "streak_days": self.streak_days(),
                     "reviews_due": reviews_due},
            "risks": [{"assessment": r.title, "subject": r.subject_name, "date": r.date.isoformat(),
                       "shortfall_min": r.shortfall_min} for r in self.plan().risks],
            "missing_info": [f"{m['subject']}: {m['field']}" if m["subject"] else
                             ("no subjects yet" if m["field"] == "subjects" else "weekly availability")
                             for m in self.missing_info()],
        }
        return f"{len(subjects)} subject(s), {len(today_blocks)} block(s) today", data

    def _t_get_plan(self, a: dict[str, Any]) -> tuple[str, Any]:
        today = self.today()
        d_from = self._date(a["date_from"]) if "date_from" in a else today
        d_to = self._date(a["date_to"]) if "date_to" in a else d_from
        if d_to < d_from:
            raise ToolError("get_plan: 'date_to' must not be before 'date_from'")
        if (d_to - d_from).days > 60:
            raise ToolError("get_plan: the range must be 60 days or less")
        subjects = {s.id: s for s in self.repo.list_subjects()}
        blocks = self.repo.list_blocks(d_from, d_to)
        days: list[dict[str, Any]] = []
        d = d_from
        while d <= d_to:
            mine = [self._block_dict(b, subjects) for b in blocks if b.plan_date == d]
            days.append({"date": d.isoformat(), "weekday": config.WEEKDAY_LONG[d.weekday()],
                         "capacity_min": self._capacity(d), "blocks": mine})
            d += timedelta(days=1)
        return f"{len(blocks)} block(s) from {d_from.isoformat()} to {d_to.isoformat()}", {"days": days}

    def _t_explain_priority(self, a: dict[str, Any]) -> tuple[str, Any]:
        plan = self.plan()
        only = self._subject(a["subject"]).id if "subject" in a else None
        rows: list[dict[str, Any]] = []
        for sid, info in plan.explanations.items():
            if only is not None and sid != only:
                continue
            days = info["days"]
            if not days:
                rows.append({"subject": info["subject"], "weight": 0.0,
                             "note": "nothing left to schedule for this subject"})
                continue
            first = days[0]
            rows.append({
                "subject": info["subject"], "date": first["date"], "weight": first["weight"],
                "urgency": first["urgency"], "days_until_assessment": first["days_until"],
                "backlog_pressure": first["backlog_pressure"], "backlog_units": first["backlog_units"],
                "difficulty_factor": first["difficulty_factor"], "mastery_deficit": first["mastery_deficit"],
                "exam_scope_boost": first["exam_scope_boost"],
                "formula": "W = urgency × backlog_pressure × difficulty_factor × mastery_deficit × exam_scope_boost",
                "minutes_per_day": {d["date"]: d["allocated_min"] for d in days[:7]}})
        rows.sort(key=lambda r: -r["weight"])
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        text = "; ".join(f"{r['subject']} W={r['weight']:.2f}" for r in rows) or "no active subjects"
        return f"Priority today: {text}", {"priorities": rows}

    def _t_get_risks(self, a: dict[str, Any]) -> tuple[str, Any]:
        risks = [self._risk_dict(r) for r in self.plan().risks]
        return (f"{len(risks)} risk(s)" if risks else "No risks: every assessment is covered"), {"risks": risks}

    def _t_list_subjects(self, a: dict[str, Any]) -> tuple[str, Any]:
        today = self.today()
        boosts = cap_boost_ids(self.repo)
        out: list[dict[str, Any]] = []
        for s in self.repo.list_subjects():
            units = self.repo.list_units(s.id)
            done = sum(1 for u in units if u.status == "done")
            out.append({"id": s.id, "name": s.name, "category": s.category, "difficulty": s.difficulty,
                        "mastery": s.mastery, "minutes_per_unit": s.minutes_per_unit,
                        "expected_units_by_now": s.expected_units_by_now, "color_hex": s.color_hex,
                        "active": s.active, "total_units": len(units), "done_units": done,
                        "backlog_units": max(0, s.expected_units_by_now - done),
                        "cap_boost": s.id in boosts,
                        "assessments": [self._assessment_dict(x, today)
                                        for x in self.repo.list_assessments(s.id)]})
        return f"{len(out)} subject(s)", {"subjects": out}

    def _t_list_reviews(self, a: dict[str, Any]) -> tuple[str, Any]:
        today = self.today()
        subjects = {s.id: s for s in self.repo.list_subjects()}
        due: list[dict[str, Any]] = []
        upcoming: list[dict[str, Any]] = []
        for r in self.repo.list_reviews():
            s = subjects.get(r.subject_id)
            if s is None or not s.active:
                continue
            row = {"id": r.id, "subject": s.name, "color": s.color_hex, "unit": r.unit_number,
                   "stage": r.stage_index, "due_date": r.due_date.isoformat(),
                   "days_overdue": (today - r.due_date).days, "last_rating": r.last_rating}
            (due if r.due_date <= today else upcoming).append(row)
        return f"{len(due)} due, {len(upcoming)} upcoming", {"due": due, "upcoming": upcoming}

    def _t_get_availability(self, a: dict[str, Any]) -> tuple[str, Any]:
        today = self.today()
        days = [{"weekday": wd, "name": config.WEEKDAY_LONG[wd], "minutes": av.minutes,
                 "start_time": av.start_time, "peak_window": av.peak_window}
                for wd, av in sorted(self.repo.get_availability().items())]
        overrides = [{"date": o.date.isoformat(), "minutes": o.minutes, "start_time": o.start_time}
                     for o in sorted(self.repo.get_overrides(today).values(), key=lambda o: o.date)]
        return "availability", {"days": days, "overrides": overrides}

    def _t_get_settings(self, a: dict[str, Any]) -> tuple[str, Any]:
        stored = self.repo.all_settings()
        values = {k: stored.get(k, config.DEFAULT_SETTINGS.get(k, "")) for k in config.EDITABLE_SETTINGS}
        values["theme"] = config.normalize_theme(values["theme"])
        for key, default in config.PANE_SETTINGS.items():
            values[key] = stored.get(key, str(default))
        for key in config.UI_PREF_BLOBS:
            values[key] = stored.get(key, "")
        return "settings", values

    def _t_set_ui_prefs(self, a: dict[str, Any]) -> tuple[str, Any]:
        """Theme, text size and divider positions; validated, then stored as plain settings."""
        clean: dict[str, str] = {}
        for key, raw in a["prefs"].items():
            if key not in config.UI_PREFS:
                raise ToolError(f"set_ui_prefs: unknown preference '{key}'")
            value = str(raw).strip()
            if key == "theme":
                value = config.LEGACY_THEMES.get(value, value)
            allowed = config.UI_PREFS[key]
            if key in config.UI_PREF_BLOBS:
                if len(value) > config.UI_PREF_BLOB_MAX or not _BLOB_RE.match(value):
                    raise ToolError(f"set_ui_prefs: '{key}' must be base64 text")
                clean[key] = value
                continue
            if allowed is not None and value not in allowed:
                raise ToolError(f"set_ui_prefs: '{key}' must be one of {', '.join(allowed)} (got {value!r})")
            if allowed is None and not (value.isdigit() and 0 < int(value) <= config.PANE_MAX_WIDTH):
                raise ToolError(f"set_ui_prefs: '{key}' must be a width in px (got {value!r})")
            clean[key] = value
        for key, value in clean.items():
            self.repo.set_setting(key, value)
        return "Preferences saved", clean

    def _t_export_data(self, a: dict[str, Any]) -> tuple[str, Any]:
        return "Exported all data", self.repo.export_all()

    # ------------------------------------------------------------ write tools: subjects & progress
    def _t_upsert_subject(self, a: dict[str, Any]) -> tuple[str, Any]:
        name = a["name"].strip()
        existing = self.repo.get_subject_by_name(name)
        fields = {k: a[k] for k in ("category", "difficulty", "mastery", "minutes_per_unit", "color_hex",
                                    "active") if k in a}
        if "new_name" in a:
            new_name = a["new_name"].strip()
            other = self.repo.get_subject_by_name(new_name)
            if other is not None and (existing is None or other.id != existing.id):
                raise ToolError(f"upsert_subject: a subject named '{new_name}' already exists")
            fields["name"] = new_name
        if existing is None:
            count = len(self.repo.list_subjects())
            defaults = dict(config.DEFAULT_SUBJECT)
            defaults.update(fields)
            sid = self.repo.insert_subject(
                defaults.get("name", name), defaults["category"], defaults["difficulty"], defaults["mastery"],
                defaults["minutes_per_unit"], 0,
                defaults.get("color_hex", config.SUBJECT_COLORS[count % len(config.SUBJECT_COLORS)]),
                defaults.get("active", True))
            verb = "Added"
        else:
            sid = existing.id
            self.repo.update_subject(sid, **fields)
            verb = "Updated"
        subject = self.repo.get_subject(sid)
        assert subject is not None
        if "total_units" in a:
            self.repo.set_total_units(sid, a["total_units"])
        elif not self.repo.list_units(sid):
            self._ensure_units(subject, max(a.get("expected_units_by_now", 0), a.get("done_units", 0)))
        total = len(self.repo.list_units(sid))
        if "done_units" in a:
            if a["done_units"] > total:
                raise ToolError(f"upsert_subject: 'done_units' ({a['done_units']}) exceeds the "
                                f"{total} unit(s) of {subject.name}")
            self._set_done_units(subject, a["done_units"])
        expected = a.get("expected_units_by_now", subject.expected_units_by_now)
        if "expected_units_by_now" in a and expected > total:
            raise ToolError(f"upsert_subject: 'expected_units_by_now' ({expected}) exceeds the "
                            f"{total} unit(s) of {subject.name}")
        self.repo.update_subject(sid, expected_units_by_now=min(expected, total) if total else expected)
        changed = [k for k in a if k not in ("name",)]
        detail = f" ({', '.join(changed)})" if changed and verb == "Updated" else ""
        if verb == "Added" and total:
            detail = f" with {total} units"
        if changed == ["active"] and existing is not None:      # the Archive / Restore buttons and "archive X"
            verb, detail = ("Restored" if subject.active else "Archived"), ""
        return f"{verb} subject {subject.name}{detail}", {"id": sid, "name": subject.name}

    def _t_delete_subject(self, a: dict[str, Any]) -> tuple[str, Any]:
        s = self._subject(a["subject"])
        self.repo.delete_subject(s.id)
        boosts = sorted(cap_boost_ids(self.repo) - {s.id})
        self.repo.set_setting("cap_boosts", json.dumps(boosts))
        return f"Deleted subject {s.name}", {"name": s.name}

    def _t_set_progress(self, a: dict[str, Any]) -> tuple[str, Any]:
        if "expected_units_by_now" not in a and "done_units" not in a:
            raise ToolError("set_progress: give 'expected_units_by_now' and/or 'done_units'")
        s = self._subject(a["subject"])
        total = len(self.repo.list_units(s.id))
        wanted = max(a.get("expected_units_by_now", 0), a.get("done_units", 0))
        if total == 0:
            self._ensure_units(s, wanted)
        elif wanted > total:
            field = "expected_units_by_now" if a.get("expected_units_by_now", 0) > total else "done_units"
            raise ToolError(f"set_progress: '{field}' ({wanted}) exceeds the {total} unit(s) of {s.name}")
        parts: list[str] = []
        if "done_units" in a:
            self._set_done_units(s, a["done_units"])
            parts.append(f"{a['done_units']} done")
        if "expected_units_by_now" in a:
            self.repo.update_subject(s.id, expected_units_by_now=a["expected_units_by_now"])
            parts.append(f"{a['expected_units_by_now']} expected by now")
            dismissed = self._dismissed() | {f"{s.id}:expected"}
            self.repo.set_setting("onboarding_skipped", json.dumps(sorted(dismissed)))
        fresh = self.repo.get_subject(s.id)
        assert fresh is not None
        done = sum(1 for u in self.repo.list_units(s.id) if u.status == "done")
        backlog = max(0, fresh.expected_units_by_now - done)
        return (f"{s.name}: {', '.join(parts)} → {backlog} unit(s) behind",
                {"subject": s.name, "backlog_units": backlog, "done_units": done,
                 "expected_units_by_now": fresh.expected_units_by_now})

    def _t_log_study(self, a: dict[str, Any]) -> tuple[str, Any]:
        if ("minutes" in a) == ("units" in a):
            raise ToolError("log_study: give either 'minutes' or 'units' (exactly one)")
        s = self._subject(a["subject"])
        today = self.today()
        undone = [u for u in self.repo.list_units(s.id) if u.status != "done"]
        if "units" in a:
            if a["units"] > len(undone):
                raise ToolError(f"log_study: {s.name} has only {len(undone)} unit(s) left, not {a['units']}")
            finished = []
            for unit in undone[:a["units"]]:
                self._mark_unit_done(s, unit, today)
                finished.append(unit.number)
            return (f"Logged {a['units']} unit(s) of {s.name} as done",
                    {"subject": s.name, "units_done": finished})
        minutes = a["minutes"]
        target = undone[0].number if undone else max((u.number for u in self.repo.list_units(s.id)), default=0)
        finished = self._apply_minutes(s, minutes)
        block_type = (config.PRACTICE if not undone else
                      config.CATCHUP if target <= s.expected_units_by_now else config.PROGRESS)
        self.repo.insert_block(StudyBlock(
            0, today, "", s.id, target, block_type, config.POMODORO[s.category], minutes,
            f"Logged study: {s.name}" + (f" unit {target}" if undone else ""), "done", minutes, False, 0))
        extra = f"; finished unit(s) {', '.join(map(str, finished))}" if finished else ""
        return f"Logged {minutes} min of {s.name}{extra}", {"subject": s.name, "units_done": finished}

    # ------------------------------------------------------------ write tools: assessments
    def _check_range(self, tool: str, subject: Subject, unit_from: int, unit_to: int) -> None:
        total = len(self.repo.list_units(subject.id))
        if unit_from > unit_to:
            raise ToolError(f"{tool}: 'unit_from' ({unit_from}) must not exceed 'unit_to' ({unit_to})")
        if total and unit_to > total:
            raise ToolError(f"{tool}: 'unit_to' ({unit_to}) exceeds the {total} unit(s) of {subject.name}")

    def _t_add_assessment(self, a: dict[str, Any]) -> tuple[str, Any]:
        s = self._subject(a["subject"])
        when = self._date(a["date"])
        if when < self.today():
            raise ToolError(f"add_assessment: 'date' {when.isoformat()} is in the past")
        total = len(self.repo.list_units(s.id))
        unit_from = a.get("unit_from", 1)
        unit_to = a.get("unit_to", max(total, unit_from))
        self._check_range("add_assessment", s, unit_from, unit_to)
        title = a.get("title", f"{s.name} {a['kind']}")
        aid = self.repo.insert_assessment(s.id, a["kind"], title, when, a.get("weight_pct", 0), unit_from, unit_to)
        return (f"Added {title} on {when.isoformat()} (units {unit_from}–{unit_to})",
                {"id": aid, "subject": s.name, "date": when.isoformat()})

    def _t_update_assessment(self, a: dict[str, Any]) -> tuple[str, Any]:
        current = self.repo.get_assessment(a["id"])
        if current is None:
            raise ToolError(f"update_assessment: no assessment with id {a['id']}")
        fields: dict[str, Any] = {k: a[k] for k in ("kind", "title", "weight_pct", "unit_from", "unit_to") if k in a}
        if "date" in a:
            fields["date"] = self._date(a["date"])
        if not fields:
            raise ToolError("update_assessment: nothing to change")
        subject = self.repo.get_subject(current.subject_id)
        assert subject is not None
        self._check_range("update_assessment", subject, fields.get("unit_from", current.unit_from),
                          fields.get("unit_to", current.unit_to))
        self.repo.update_assessment(current.id, **fields)
        shown = ", ".join(f"{k}={v.isoformat() if isinstance(v, date) else v}" for k, v in fields.items())
        return f"Updated {current.title}: {shown}", {"id": current.id}

    def _t_delete_assessment(self, a: dict[str, Any]) -> tuple[str, Any]:
        current = self.repo.get_assessment(a["id"])
        if current is None:
            raise ToolError(f"delete_assessment: no assessment with id {a['id']}")
        self.repo.delete_assessment(current.id)
        return f"Deleted {current.title}", {"id": current.id}

    # ------------------------------------------------------------ write tools: availability
    def _t_set_weekly_availability(self, a: dict[str, Any]) -> tuple[str, Any]:
        current = self.repo.get_availability()
        changed: list[str] = []
        for entry in a["days"]:
            wd = entry["weekday"]
            if isinstance(wd, str):
                key = wd.strip().lower()
                if key not in _WEEKDAY_LOOKUP:
                    raise ToolError(f"set_weekly_availability: unknown weekday '{wd}' (use mon..sun or 0..6)")
                wd = _WEEKDAY_LOOKUP[key]
            if not 0 <= wd <= 6:
                raise ToolError(f"set_weekly_availability: 'weekday' must be between 0 and 6 (got {wd})")
            if not any(k in entry for k in ("minutes", "start_time", "peak_window")):
                raise ToolError("set_weekly_availability: each day needs minutes, start_time or peak_window")
            av = current[wd]
            av.minutes = entry.get("minutes", av.minutes)
            av.start_time = entry.get("start_time", av.start_time)
            av.peak_window = entry.get("peak_window", av.peak_window)
            self.repo.set_availability(wd, av.minutes, av.start_time, av.peak_window)
            changed.append(f"{config.WEEKDAY_NAMES[wd].capitalize()} {av.minutes} min")
        return f"Availability set: {', '.join(changed)}", {"days": len(changed)}

    def _t_set_day_override(self, a: dict[str, Any]) -> tuple[str, Any]:
        when = self._date(a["date"])
        if when < self.today():
            raise ToolError(f"set_day_override: 'date' {when.isoformat()} is in the past")
        if a.get("clear"):
            self.repo.delete_override(when)
            return f"Removed the override for {when.isoformat()}", {"date": when.isoformat()}
        if "minutes" not in a:
            raise ToolError("set_day_override: 'minutes' is required")
        self.repo.set_override(when, a["minutes"], a.get("start_time"))
        label = "today" if when == self.today() else when.isoformat()
        return f"Set {label} to {a['minutes']} min", {"date": when.isoformat(), "minutes": a["minutes"]}

    # ------------------------------------------------------------ write tools: blocks & reviews
    def _t_complete_block(self, a: dict[str, Any]) -> tuple[str, Any]:
        block = self._pending_block(a["id"])
        status = a.get("status", "done")
        if status == "partial" and "actual_min" not in a:
            raise ToolError("complete_block: 'actual_min' is required for a partial block")
        actual = a.get("actual_min", block.duration_min)
        subject = self.repo.get_subject(block.subject_id)
        if subject is None:
            raise ToolError(f"complete_block: block {block.id} has no subject")
        today = self.today()
        fields: dict[str, Any] = {"status": status, "actual_min": actual}
        if block.plan_date > today:
            fields["plan_date"] = today
        self.repo.update_block(block.id, **fields)
        finished: list[int] = []
        note = ""
        if block.block_type in (config.CATCHUP, config.PROGRESS):
            finished = self._apply_minutes(subject, actual, block.unit_number)
            if finished:
                note = f"; unit(s) {', '.join(map(str, finished))} done, first review tomorrow"
        elif block.block_type == config.REVIEW:
            rating = a.get("rating", "good")
            covered = set(review_units(block))
            for review in self.repo.list_reviews():
                if review.subject_id == subject.id and review.unit_number in covered:
                    self._apply_review_rating(review, rating)
            note = f"; rated {rating}"
        self._queue(events.BLOCK_FINISHED, block_id=block.id, subject=subject.name, status=status,
                    actual_min=actual, block_type=block.block_type, units_done=finished)
        label = "Done" if status == "done" else "Partial"
        return (f"{label}: {subject.name} {block.block_type.lower()} block, {actual} min{note}",
                {"id": block.id, "status": status, "actual_min": actual, "units_done": finished})

    def _t_skip_block(self, a: dict[str, Any]) -> tuple[str, Any]:
        block = self._pending_block(a["id"])
        subject = self.repo.get_subject(block.subject_id)
        self.repo.update_block(block.id, status="skipped", actual_min=0)
        name = subject.name if subject else "?"
        self._queue(events.BLOCK_SKIPPED, block_id=block.id, subject=name, reason=a.get("reason", ""),
                    block_type=block.block_type)
        return f"Skipped: {name} {block.block_type.lower()} block ({block.duration_min} min)", {"id": block.id}

    def _t_lock_block(self, a: dict[str, Any]) -> tuple[str, Any]:
        block = self._block(a["id"])
        locked = a.get("locked", True)
        self.repo.update_block(block.id, locked=locked)
        return f"{'Locked' if locked else 'Unlocked'} block {block.id}", {"id": block.id, "locked": locked}

    def _t_move_block(self, a: dict[str, Any]) -> tuple[str, Any]:
        block = self._pending_block(a["id"])
        new_date = self._date(a["new_date"])
        if new_date < self.today():
            raise ToolError(f"move_block: 'new_date' {new_date.isoformat()} is in the past")
        availability = self.repo.get_availability()[new_date.weekday()]
        override = self.repo.get_overrides(new_date).get(new_date)
        start = (override.start_time if override and override.start_time else availability.start_time)
        self.repo.update_block(block.id, plan_date=new_date, locked=True, start_time=start, order_index=0)
        return f"Moved block {block.id} to {new_date.isoformat()} (locked)", {"id": block.id}

    def _t_complete_review(self, a: dict[str, Any]) -> tuple[str, Any]:
        review = self.repo.get_review(a["id"])
        if review is None:
            raise ToolError(f"complete_review: no review with id {a['id']}")
        if review.done:
            raise ToolError(f"complete_review: review {review.id} is already finished")
        subject = self.repo.get_subject(review.subject_id)
        outcome = self._apply_review_rating(review, a["rating"])
        name = subject.name if subject else "?"
        return (f"Review {name} unit {review.unit_number}: {a['rating']} → {outcome}",
                {"id": review.id, "rating": a["rating"]})

    # ------------------------------------------------------------ write tools: plan & misc
    def _t_regenerate_plan(self, a: dict[str, Any]) -> tuple[str, Any]:
        return f"Plan rebuilt for the next {horizon_days(self.repo)} days", {}

    def _t_undo_last(self, a: dict[str, Any]) -> tuple[str, Any]:
        entry = self.repo.last_undoable()
        if entry is None or entry.inverse is None:
            raise ToolError("Nothing to undo")
        if "snapshot" in entry.inverse:
            self.repo.restore(entry.inverse["snapshot"])
        for key, value in (entry.inverse.get("settings_prev") or {}).items():
            self.repo.set_setting(key, value)
        self.repo.mark_undone(entry.id)
        return f"Undid {entry.tool}", {"undone_action_id": entry.id, "tool": entry.tool}

    def _t_set_cap_boost(self, a: dict[str, Any]) -> tuple[str, Any]:
        s = self._subject(a["subject"])
        boosts = set(cap_boost_ids(self.repo))
        if a["enabled"]:
            boosts.add(s.id)
        else:
            boosts.discard(s.id)
        self.repo.set_setting("cap_boosts", json.dumps(sorted(boosts)))
        pct = int((config.DAILY_CAP_SHARE_EXAM if a["enabled"] else config.DAILY_CAP_SHARE) * 100)
        return f"{s.name}: daily cap set to {pct}%", {"subject": s.name, "enabled": a["enabled"]}

    def _t_dismiss_missing_info(self, a: dict[str, Any]) -> tuple[str, Any]:
        s = self._subject(a["subject"])
        dismissed = self._dismissed() | {f"{s.id}:{a['field']}"}
        self.repo.set_setting("onboarding_skipped", json.dumps(sorted(dismissed)))
        return f"OK, not asking about {s.name}'s {a['field']} again", {"subject": s.name}

    def _t_update_settings(self, a: dict[str, Any]) -> tuple[str, Any]:
        previous: dict[str, str] = {}
        for key, raw in a["settings"].items():
            if key not in config.EDITABLE_SETTINGS:
                raise ToolError(f"update_settings: unknown setting '{key}'")
            value = str(raw).strip()
            if key == "base_url":
                value = config.clean_base_url(value)
            elif key in config.TEXT_SETTINGS:
                value = config.clean_pasted(value)
            elif key == "theme":
                value = config.LEGACY_THEMES.get(value, value)
            allowed = config.EDITABLE_SETTINGS[key]
            if allowed is not None and value not in allowed:
                raise ToolError(f"update_settings: '{key}' must be one of {', '.join(allowed)} (got {value!r})")
            if key == "daily_llm_call_cap" and not (value.isdigit() and int(value) <= 10000):
                raise ToolError("update_settings: 'daily_llm_call_cap' must be a whole number from 0 to 10000")
            if key == "plan_horizon_days" and not (value.isdigit() and 1 <= int(value) <= 60):
                raise ToolError("update_settings: 'plan_horizon_days' must be between 1 and 60")
            if key == "end_of_day_time" and not _TIME_RE.match(value):
                raise ToolError("update_settings: 'end_of_day_time' must be a time like 22:00")
            old = self.repo.get_setting(key, config.DEFAULT_SETTINGS.get(key, "")) or ""
            if old != value:
                previous[key] = old
                self.repo.set_setting(key, value)
                if key == "plan_horizon_days":
                    self._force_regen = True
        names = ", ".join(previous) or "nothing changed"
        return f"Settings saved ({names})", {"changed": sorted(previous), "_settings_prev": previous}

    def _t_reset_demo_data(self, a: dict[str, Any]) -> tuple[str, Any]:
        from ..seed import seed_demo
        self.repo.clear_all()
        seed_demo(self.repo, self.today(), generate=False)
        return "Demo data restored", {}

    def _t_import_data(self, a: dict[str, Any]) -> tuple[str, Any]:
        if a["data"].get("format") != "neuroplan-export-1":
            raise ToolError("import_data: this file is not a NeuroPlan export")
        try:
            self.repo.import_all(a["data"])
        except Exception as exc:
            raise ToolError(f"import_data: the file could not be imported ({exc})") from exc
        return "Data imported", {}
