"""P3: tool registry, executor, validation, undo, confirmation."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from neuroplan import config, events
from neuroplan.db.repository import Repository
from neuroplan.events import Event, EventBus
from neuroplan.tools import registry
from neuroplan.tools.executor import ToolExecutor, describe_diff

from .conftest import TODAY

ISO = TODAY.isoformat()
FUTURE = (TODAY + timedelta(days=9)).isoformat()
PAST = (TODAY - timedelta(days=1)).isoformat()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ex(seeded: Repository, bus: EventBus) -> ToolExecutor:
    return ToolExecutor(seeded, bus, today_fn=lambda: TODAY)


def first_pending(repo: Repository, block_type: str | None = None) -> int:
    for b in repo.list_blocks(TODAY, TODAY):
        if b.status == "pending" and (block_type is None or b.block_type == block_type):
            return b.id
    raise AssertionError("no pending block today")


# ---------------------------------------------------------------- registry
def test_registry_exports_both_formats() -> None:
    anthropic = registry.anthropic_tools()
    openai = registry.openai_tools()
    names = {t["name"] for t in anthropic}
    required = {"get_state", "get_plan", "explain_priority", "get_risks", "upsert_subject", "delete_subject",
                "set_progress", "log_study", "add_assessment", "update_assessment", "delete_assessment",
                "set_weekly_availability", "set_day_override", "complete_block", "skip_block", "lock_block",
                "move_block", "complete_review", "regenerate_plan", "undo_last"}
    assert required <= names
    assert names == {t["function"]["name"] for t in openai}
    for tool in anthropic:
        assert tool["input_schema"]["type"] == "object" and tool["description"]
    for tool in openai:
        assert tool["type"] == "function" and tool["function"]["parameters"]["type"] == "object"
    assert "update_settings" not in names and "reset_demo_data" not in names
    assert registry.TOOLS["delete_subject"].requires_confirmation


def test_every_tool_has_a_handler(ex: ToolExecutor) -> None:
    for name in registry.TOOLS:
        assert callable(getattr(ex, "_t_" + name)), name


# ---------------------------------------------------------------- validation
BAD_CALLS: list[tuple[str, dict[str, Any], str]] = [
    ("upsert_subject", {}, "'name' is required"),
    ("upsert_subject", {"name": "X", "difficulty": 9}, "'difficulty' must be between 1 and 5"),
    ("upsert_subject", {"name": "X", "mastery": 0}, "'mastery' must be between 1 and 5"),
    ("upsert_subject", {"name": "X", "category": "EASY"}, "'category' must be one of"),
    ("upsert_subject", {"name": "  "}, "'name' must not be empty"),
    ("upsert_subject", {"name": "X", "color_hex": "blue"}, "invalid format"),
    ("upsert_subject", {"name": "X", "bogus": 1}, "Unknown argument 'bogus'"),
    ("upsert_subject", {"name": "Deep Learning", "expected_units_by_now": 99}, "exceeds the 12 unit(s)"),
    ("upsert_subject", {"name": "Deep Learning", "new_name": "Machine Learning"}, "already exists"),
    ("delete_subject", {"subject": "Astrology", "confirmed": True}, "Unknown subject 'Astrology'"),
    ("set_progress", {"subject": "Deep Learning"}, "give 'expected_units_by_now' and/or 'done_units'"),
    ("set_progress", {"subject": "Nope", "done_units": 1}, "Unknown subject 'Nope'"),
    ("set_progress", {"subject": "Deep Learning", "done_units": -1}, "'done_units' must be between 0 and 200"),
    ("set_progress", {"subject": "Deep Learning", "expected_units_by_now": 99}, "exceeds the 12 unit(s)"),
    ("set_progress", {"subject": "Deep Learning", "done_units": 13}, "'done_units' (13) exceeds"),
    ("log_study", {"subject": "Deep Learning"}, "either 'minutes' or 'units'"),
    ("log_study", {"subject": "Deep Learning", "minutes": 30, "units": 1}, "either 'minutes' or 'units'"),
    ("log_study", {"subject": "Deep Learning", "minutes": 0}, "'minutes' must be between 1 and 600"),
    ("log_study", {"subject": "Deep Learning", "units": 40}, "only 9 unit(s) left"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "midterm"}, "'date' is required"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "viva", "date": FUTURE}, "'kind' must be one of"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "quiz", "date": "next week"}, "must be a date like"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "quiz", "date": PAST}, "is in the past"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "quiz", "date": FUTURE, "unit_from": 5,
                        "unit_to": 2}, "must not exceed 'unit_to'"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "quiz", "date": FUTURE, "unit_to": 40},
     "exceeds the 12 unit(s)"),
    ("add_assessment", {"subject": "Deep Learning", "kind": "quiz", "date": FUTURE, "weight_pct": 150},
     "'weight_pct' must be between 0 and 100"),
    ("update_assessment", {"id": 9999, "title": "x"}, "no assessment with id 9999"),
    ("update_assessment", {"id": 1}, "nothing to change"),
    ("delete_assessment", {"id": 9999}, "no assessment with id 9999"),
    ("set_weekly_availability", {"days": []}, "at least 1 item"),
    ("set_weekly_availability", {"days": [{"weekday": "funday", "minutes": 60}]}, "unknown weekday 'funday'"),
    ("set_weekly_availability", {"days": [{"weekday": 0, "minutes": 5000}]}, "must be between 0 and 960"),
    ("set_weekly_availability", {"days": [{"weekday": 0, "start_time": "25:00"}]}, "must be a time like"),
    ("set_weekly_availability", {"days": [{"weekday": 0, "peak_window": "dawn"}]}, "must be one of"),
    ("set_weekly_availability", {"days": [{"weekday": 0}]}, "needs minutes, start_time or peak_window"),
    ("set_day_override", {"date": ISO}, "'minutes' is required"),
    ("set_day_override", {"date": ISO, "minutes": -5}, "must be between 0 and 960"),
    ("set_day_override", {"date": PAST, "minutes": 60}, "is in the past"),
    ("set_day_override", {"date": "soon", "minutes": 60}, "must be a date like"),
    ("complete_block", {"id": 99999}, "No study block with id 99999"),
    ("complete_block", {"id": "abc"}, "'id' must be a whole number"),
    ("complete_block", {"id": 1, "status": "finished"}, "'status' must be one of"),
    ("skip_block", {"id": 99999}, "No study block with id 99999"),
    ("skip_block", {}, "'id' is required"),
    ("lock_block", {"id": 99999}, "No study block with id 99999"),
    ("move_block", {"id": 99999, "new_date": FUTURE}, "No study block with id 99999"),
    ("move_block", {"id": 1}, "'new_date' is required"),
    ("complete_review", {"id": 99999, "rating": "good"}, "no review with id 99999"),
    ("complete_review", {"id": 1, "rating": "perfect"}, "'rating' must be one of"),
    ("set_cap_boost", {"subject": "Deep Learning"}, "'enabled' is required"),
    ("regenerate_plan", {"force": True}, "Unknown argument 'force'"),
    ("get_plan", {"date_from": FUTURE, "date_to": ISO}, "must not be before"),
    ("explain_priority", {"subject": "Astrology"}, "Unknown subject"),
    ("update_settings", {"settings": {"provider": "skynet"}}, "'provider' must be one of"),
    ("update_settings", {"settings": {"plan_horizon_days": "0"}}, "between 1 and 60"),
    ("update_settings", {"settings": {"nope": "1"}}, "unknown setting 'nope'"),
    ("no_such_tool", {}, "Unknown tool 'no_such_tool'"),
]


@pytest.mark.parametrize("tool,args,message", BAD_CALLS, ids=[f"{t}-{i}" for i, (t, _, _) in enumerate(BAD_CALLS)])
def test_bad_input_gives_clear_error_and_changes_nothing(ex: ToolExecutor, seeded: Repository, tool: str,
                                                         args: dict[str, Any], message: str) -> None:
    before = seeded.snapshot()
    actions = len(seeded.list_actions())
    result = ex.execute(tool, args)
    assert not result.ok
    assert message in result.summary, result.summary
    assert seeded.snapshot() == before
    assert len(seeded.list_actions()) == actions


def test_partial_block_requires_minutes_and_double_completion_fails(ex: ToolExecutor, seeded: Repository) -> None:
    block_id = first_pending(seeded, config.CATCHUP)
    result = ex.execute("complete_block", {"id": block_id, "status": "partial"})
    assert not result.ok and "'actual_min' is required for a partial block" in result.summary
    assert ex.execute("complete_block", {"id": block_id, "status": "partial", "actual_min": 20}).ok
    block = seeded.get_block(block_id)
    assert block is not None and (block.status, block.actual_min) == ("partial", 20)
    again = ex.execute("complete_block", {"id": block_id})
    assert not again.ok and "already partial" in again.summary
    skipped = ex.execute("skip_block", {"id": block_id})
    assert not skipped.ok and "already partial" in skipped.summary


def test_invalid_source_rejected(ex: ToolExecutor) -> None:
    assert not ex.execute("regenerate_plan", {}, source="hacker").ok


# ---------------------------------------------------------------- undo
def assert_undo_restores(ex: ToolExecutor, repo: Repository, tool: str, args: dict[str, Any]) -> None:
    before = repo.snapshot()
    result = ex.execute(tool, args)
    assert result.ok, result.summary
    assert repo.snapshot() != before, f"{tool} should change state"
    undone = ex.execute("undo_last")
    assert undone.ok and tool in undone.summary
    assert repo.snapshot() == before, f"undo of {tool} must restore the exact previous state"


def test_undo_upsert_subject_create(ex: ToolExecutor, seeded: Repository) -> None:
    assert_undo_restores(ex, seeded, "upsert_subject",
                         {"name": "Databases", "category": "CODING", "total_units": 8, "difficulty": 4})


def test_undo_upsert_subject_update(ex: ToolExecutor, seeded: Repository) -> None:
    assert_undo_restores(ex, seeded, "upsert_subject",
                         {"name": "Deep Learning", "mastery": 5, "total_units": 6, "new_name": "DL 2"})


def test_undo_set_progress(ex: ToolExecutor, seeded: Repository) -> None:
    assert_undo_restores(ex, seeded, "set_progress",
                         {"subject": "Machine Learning", "expected_units_by_now": 9, "done_units": 2})


def test_undo_set_day_override(ex: ToolExecutor, seeded: Repository) -> None:
    assert_undo_restores(ex, seeded, "set_day_override", {"date": "today", "minutes": 90})


def test_undo_complete_block(ex: ToolExecutor, seeded: Repository) -> None:
    assert_undo_restores(ex, seeded, "complete_block", {"id": first_pending(seeded, config.CATCHUP)})


@pytest.mark.parametrize("tool,args", [
    ("log_study", {"subject": "IELTS Prep", "minutes": 40}),
    ("log_study", {"subject": "Deep Learning", "units": 2}),
    ("add_assessment", {"subject": "IELTS Prep", "kind": "quiz", "date": FUTURE}),
    ("set_weekly_availability", {"days": [{"weekday": "mon", "minutes": 60}, {"weekday": 4, "minutes": 0}]}),
    ("delete_subject", {"subject": "Internet Programming", "confirmed": True}),
    ("set_cap_boost", {"subject": "Deep Learning", "enabled": True}),
])
def test_undo_other_write_tools(ex: ToolExecutor, seeded: Repository, tool: str, args: dict[str, Any]) -> None:
    assert_undo_restores(ex, seeded, tool, args)


def test_undo_is_stacked_and_runs_out(ex: ToolExecutor, seeded: Repository) -> None:
    s0 = seeded.snapshot()
    assert ex.execute("set_day_override", {"date": ISO, "minutes": 60}).ok
    s1 = seeded.snapshot()
    assert ex.execute("upsert_subject", {"name": "Deep Learning", "mastery": 4}).ok
    assert ex.execute("undo_last").ok
    assert seeded.snapshot() == s1
    assert ex.execute("undo_last").ok
    assert seeded.snapshot() == s0
    nothing = ex.execute("undo_last")
    assert not nothing.ok and "Nothing to undo" in nothing.summary


def test_actions_are_logged_with_source(ex: ToolExecutor, seeded: Repository) -> None:
    result = ex.execute("set_day_override", {"date": ISO, "minutes": 60}, source="chat_offline")
    assert result.ok and result.action_id
    entry = seeded.list_actions(1)[0]
    assert (entry.id, entry.source, entry.tool, entry.undone) == (result.action_id, "chat_offline",
                                                                 "set_day_override", False)
    assert entry.args == {"date": ISO, "minutes": 60} and entry.inverse is not None
    ex.execute("get_state")
    assert len(seeded.list_actions()) == 1, "read tools are not logged"
    ex.execute("undo_last")
    assert [a.tool for a in seeded.list_actions()] == ["undo_last", "set_day_override"]
    assert seeded.list_actions()[1].undone is True


# ---------------------------------------------------------------- confirmation
def test_delete_subject_requires_confirmation(ex: ToolExecutor, seeded: Repository) -> None:
    before = seeded.snapshot()
    result = ex.execute("delete_subject", {"subject": "Internet Programming"})
    assert result.ok and result.needs_confirmation
    assert "Internet Programming" in result.preview and "10 unit(s)" in result.preview
    assert result.to_dict()["needs_confirmation"] is True
    assert seeded.snapshot() == before
    assert result.data == {"tool": "delete_subject",
                           "args": {"subject": "Internet Programming", "confirmed": True}}
    confirmed = ex.execute(result.data["tool"], result.data["args"])
    assert confirmed.ok and not confirmed.needs_confirmation
    assert seeded.get_subject_by_name("Internet Programming") is None
    assert all(b.subject_id in {s.id for s in seeded.list_subjects()} for b in seeded.list_blocks(TODAY))


def test_reset_demo_data_requires_confirmation(ex: ToolExecutor, seeded: Repository) -> None:
    ex.execute("delete_subject", {"subject": "IELTS Prep", "confirmed": True})
    preview = ex.execute("reset_demo_data")
    assert preview.needs_confirmation and len(seeded.list_subjects()) == 3
    assert ex.execute("reset_demo_data", {"confirmed": True}).ok
    assert len(seeded.list_subjects()) == 4 and seeded.list_blocks(TODAY)


# ---------------------------------------------------------------- behaviour
def test_get_state_snapshot(ex: ToolExecutor) -> None:
    data = ex.execute("get_state").data
    assert data["today"] == ISO and data["weekday"] == "Tuesday"
    dl = next(s for s in data["subjects"] if s["name"] == "Deep Learning")
    assert dl["backlog_units"] == 4 and dl["units_done"] == 3 and dl["remaining_min"] == 9 * 75
    assert dl["next_assessment"]["days_until"] == 10 and dl["next_assessment"]["kind"] == "midterm"
    assert data["kpis"]["backlog_units_total"] == 8
    assert data["kpis"]["reviews_due"] == 4
    assert data["kpis"]["planned_min_today"] == sum(b["min"] for b in data["today_blocks"])
    assert data["availability"]["fri"] == {"min": 300, "start": "10:00", "peak": "morning"}
    assert data["missing_info"] == [] and data["risks"] == []


def test_get_plan_defaults_to_today(ex: ToolExecutor) -> None:
    data = ex.execute("get_plan").data
    assert len(data["days"]) == 1 and data["days"][0]["date"] == ISO
    assert data["days"][0]["capacity_min"] == 180 and data["days"][0]["blocks"]
    week = ex.execute("get_plan", {"date_from": "today", "date_to": (TODAY + timedelta(days=6)).isoformat()}).data
    assert len(week["days"]) == 7 and all(day["blocks"] for day in week["days"])


def test_explain_priority_quotes_real_numbers(ex: ToolExecutor) -> None:
    result = ex.execute("explain_priority", {"subject": "deep learning"})
    row = result.data["priorities"][0]
    assert row["subject"] == "Deep Learning" and row["urgency"] == pytest.approx(2.4)
    assert row["weight"] == pytest.approx(2.4 * 2.8 * 1.3 * 1.36 * 1.25, abs=0.01)
    assert len(row["minutes_per_day"]) == 7
    everyone = ex.execute("explain_priority").data["priorities"]
    assert [r["rank"] for r in everyone] == [1, 2, 3, 4]
    assert everyone[0]["subject"] == "Deep Learning"


def test_subject_reference_by_acronym_and_id(ex: ToolExecutor, seeded: Repository) -> None:
    assert ex.execute("set_progress", {"subject": "ML", "expected_units_by_now": 7}).ok
    ml = seeded.get_subject_by_name("Machine Learning")
    assert ml is not None and ml.expected_units_by_now == 7
    assert ex.execute("set_progress", {"subject": ml.id, "expected_units_by_now": 8}).ok
    fresh = seeded.get_subject(ml.id)
    assert fresh is not None and fresh.expected_units_by_now == 8


def test_upsert_creates_units_and_plan(ex: ToolExecutor, seeded: Repository) -> None:
    result = ex.execute("upsert_subject", {"name": "Databases", "category": "CODING", "total_units": 8,
                                           "expected_units_by_now": 3, "done_units": 1})
    assert result.ok and "Added subject Databases" in result.summary
    s = seeded.get_subject_by_name("Databases")
    assert s is not None and s.expected_units_by_now == 3
    units = seeded.list_units(s.id)
    assert len(units) == 8 and [u.status for u in units[:2]] == ["done", "todo"]
    assert any(b.subject_id == s.id for b in seeded.list_blocks(TODAY))


def test_set_progress_done_units_marks_and_unmarks(ex: ToolExecutor, seeded: Repository) -> None:
    ml = seeded.get_subject_by_name("Machine Learning")
    assert ml is not None
    assert ex.execute("set_progress", {"subject": "Machine Learning", "done_units": 6}).ok
    units = seeded.list_units(ml.id)
    assert [u.status for u in units[:7]] == ["done"] * 6 + ["todo"]
    assert {r.unit_number for r in seeded.list_reviews() if r.subject_id == ml.id} == {1, 2, 3, 4, 5, 6}
    assert ex.execute("set_progress", {"subject": "Machine Learning", "done_units": 2}).ok
    units = seeded.list_units(ml.id)
    assert [u.status for u in units[:4]] == ["done", "done", "todo", "todo"]
    assert {r.unit_number for r in seeded.list_reviews() if r.subject_id == ml.id} == {1, 2}


def test_log_study_minutes_counts_against_today(ex: ToolExecutor, seeded: Repository) -> None:
    ml = seeded.get_subject_by_name("Machine Learning")
    assert ml is not None
    result = ex.execute("log_study", {"subject": "Machine Learning", "minutes": 40})
    assert result.ok and "Logged 40 min of Machine Learning" in result.summary
    unit5 = next(u for u in seeded.list_units(ml.id) if u.number == 5)
    assert (unit5.minutes_logged, unit5.status) == (40, "in_progress")
    logged = [b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "done"]
    assert len(logged) == 1 and logged[0].actual_min == 40 and logged[0].subject_id == ml.id
    data = ex.execute("get_state").data
    assert data["kpis"]["done_min_today"] == 40 and data["kpis"]["streak_days"] == 1


def test_skip_block_moves_work_and_emits_event(ex: ToolExecutor, seeded: Repository, bus: EventBus) -> None:
    seen: list[Event] = []
    bus.subscribe("*", seen.append)
    block_id = first_pending(seeded, config.CATCHUP)
    block = seeded.get_block(block_id)
    assert block is not None
    assert ex.execute("skip_block", {"id": block_id, "reason": "No time"}).ok
    after = seeded.get_block(block_id)
    assert after is not None and after.status == "skipped"
    names = [e.name for e in seen]
    assert names[0] == events.STATE_CHANGED and events.BLOCK_SKIPPED in names
    skipped = next(e for e in seen if e.name == events.BLOCK_SKIPPED)
    assert skipped.payload["block_id"] == block_id and skipped.payload["source"] == "ui"
    future = [b for b in seeded.list_blocks(TODAY + timedelta(days=1)) if b.subject_id == block.subject_id
              and b.block_type == config.CATCHUP]
    assert future and future[0].unit_number == block.unit_number


def test_regeneration_keeps_ids_of_unchanged_blocks(ex: ToolExecutor, seeded: Repository) -> None:
    before = {b.id: b for b in seeded.list_blocks(TODAY)}
    assert ex.execute("regenerate_plan").ok
    assert {b.id: b for b in seeded.list_blocks(TODAY)} == before
    # an unrelated change far in the future leaves today's blocks (and their ids) alone
    far = (TODAY + timedelta(days=13)).isoformat()
    assert ex.execute("set_day_override", {"date": far, "minutes": 0}).ok
    today_before = [b for b in before.values() if b.plan_date == TODAY]
    assert seeded.list_blocks(TODAY, TODAY) == sorted(today_before, key=lambda b: (b.start_time, b.order_index))


def test_last_block_of_day_flag(ex: ToolExecutor, seeded: Repository, bus: EventBus) -> None:
    seen: list[Event] = []
    bus.subscribe(events.BLOCK_FINISHED, seen.append)
    for _ in range(20):
        pending = [b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "pending"]
        if not pending:
            break
        assert ex.execute("complete_block", {"id": pending[0].id}).ok
    assert seen and seen[-1].payload["last_of_day"] is True
    assert all(e.payload["last_of_day"] is False for e in seen[:-1])


def test_lock_and_move_block(ex: ToolExecutor, seeded: Repository) -> None:
    block_id = first_pending(seeded, config.CATCHUP)
    assert ex.execute("lock_block", {"id": block_id}).ok
    assert ex.execute("regenerate_plan").ok
    locked = seeded.get_block(block_id)
    assert locked is not None and locked.locked and locked.status == "pending"
    assert ex.execute("lock_block", {"id": block_id, "locked": False}).ok
    target = TODAY + timedelta(days=2)
    other = first_pending(seeded, config.CATCHUP)
    assert ex.execute("move_block", {"id": other, "new_date": target.isoformat()}).ok
    moved = seeded.get_block(other)
    assert moved is not None and moved.plan_date == target and moved.locked


def test_plan_changed_event_carries_diff(ex: ToolExecutor, bus: EventBus) -> None:
    seen: list[Event] = []
    bus.subscribe(events.PLAN_CHANGED, seen.append)
    assert ex.execute("set_day_override", {"date": ISO, "minutes": 60}).ok
    assert len(seen) == 1
    diff = seen[0].payload["diff"]
    assert diff and all({"date", "subject", "delta_min"} <= set(d) for d in diff)
    assert any(d["date"] == ISO and d["delta_min"] < 0 for d in diff)
    assert seen[0].payload["summary"].startswith("Today:")
    assert describe_diff([{"date": ISO, "subject": "X", "delta_min": -50}], TODAY) == "Today: X −50 min"


def test_risk_detected_event_fires_once_per_new_risk(ex: ToolExecutor, seeded: Repository, bus: EventBus) -> None:
    seen: list[Event] = []
    bus.subscribe(events.RISK_DETECTED, seen.append)
    dl = seeded.get_subject_by_name("Deep Learning")
    assert dl is not None
    midterm = seeded.list_assessments(dl.id)[0]
    soon = (TODAY + timedelta(days=1)).isoformat()
    assert ex.execute("update_assessment", {"id": midterm.id, "date": soon}).ok
    assert len(seen) == 1
    risk = seen[0].payload["risks"][0]
    assert risk["subject"] == "Deep Learning" and risk["shortfall_min"] > 0 and len(risk["options"]) == 3
    assert ex.execute("regenerate_plan").ok
    assert len(seen) == 1, "a known risk is not announced again"
    risks = ex.execute("get_risks").data["risks"]
    assert risks and risks[0]["assessment_id"] == midterm.id
    # executing the computed 'extra time' option goes through ordinary tools
    for action in risk["options"][0]["actions"]:
        assert ex.execute(action["tool"], action["args"]).ok


def test_settings_roundtrip_and_undo(ex: ToolExecutor, seeded: Repository) -> None:
    result = ex.execute("update_settings", {"settings": {"proactive_level": "light", "plan_horizon_days": 7}})
    assert result.ok and result.data["changed"] == ["plan_horizon_days", "proactive_level"]
    assert ex.execute("get_settings").data["proactive_level"] == "light"
    last = max(b.plan_date for b in seeded.list_blocks(TODAY))
    assert last == TODAY + timedelta(days=6)
    assert ex.execute("undo_last").ok
    assert seeded.get_setting("proactive_level") == "full" and seeded.get_setting("plan_horizon_days") == "14"


def test_export_import_through_tools(ex: ToolExecutor, seeded: Repository) -> None:
    exported = ex.execute("export_data").data
    snapshot = seeded.snapshot()
    ex.execute("delete_subject", {"subject": "IELTS Prep", "confirmed": True})
    assert ex.execute("import_data", {"data": {"hello": 1}}).ok is False
    preview = ex.execute("import_data", {"data": exported})
    assert preview.needs_confirmation
    assert ex.execute("import_data", {"data": exported, "confirmed": True}).ok
    assert seeded.snapshot() == snapshot


def test_missing_info_and_dismiss(repo: Repository) -> None:
    ex = ToolExecutor(repo, today_fn=lambda: TODAY)
    assert ex.missing_info() == [{"subject": None, "field": "subjects"}]
    assert ex.execute("upsert_subject", {"name": "Physics"}).ok
    assert [m["field"] for m in ex.missing_info()] == ["availability", "units", "assessment", "expected"]
    assert ex.execute("set_weekly_availability", {"days": [{"weekday": "mon", "minutes": 120}]}).ok
    assert [m["field"] for m in ex.missing_info()] == ["units", "assessment", "expected"]
    assert ex.execute("upsert_subject", {"name": "Physics", "total_units": 10}).ok
    assert ex.execute("set_progress", {"subject": "Physics", "expected_units_by_now": 0}).ok
    assert [m["field"] for m in ex.missing_info()] == ["assessment"]
    assert ex.execute("dismiss_missing_info", {"subject": "Physics", "field": "assessment"}).ok
    assert ex.missing_info() == []


def test_internal_error_rolls_back_and_reports(ex: ToolExecutor, seeded: Repository, bus: EventBus,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[Event] = []
    bus.subscribe(events.ERROR, errors.append)
    before = seeded.snapshot()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("neuroplan.tools.executor.regenerate", boom)
    result = ex.execute("set_day_override", {"date": ISO, "minutes": 30})
    assert not result.ok and "disk on fire" in result.summary
    assert seeded.snapshot() == before, "the transaction is rolled back"
    assert len(errors) == 1 and "Nothing was changed" in errors[0].payload["message"]


def test_undo_skips_system_housekeeping_and_locks(ex: ToolExecutor, seeded: Repository) -> None:
    before = seeded.snapshot()
    assert ex.execute("set_day_override", {"date": ISO, "minutes": 90}, "chat_offline").ok
    assert ex.execute("regenerate_plan", {}, "system").ok
    assert ex.execute("lock_block", {"id": first_pending(seeded)}, "ui").ok
    undone = ex.execute("undo_last")
    assert undone.ok and "set_day_override" in undone.summary
    assert seeded.snapshot() == before, "the lock and the system re-plan are rolled back with it"
    assert not ex.execute("undo_last").ok


def test_exported_schemas_are_plain(ex: ToolExecutor) -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert "format" not in node
            assert not isinstance(node.get("type"), list)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(registry.anthropic_tools())
    walk(registry.openai_tools())
    # the executor itself still accepts ids and names for subjects, and 'today' for dates
    assert ex.execute("get_plan", {"date_from": "today"}).ok
