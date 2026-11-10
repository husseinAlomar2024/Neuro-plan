"""Acceptance 8b: the pomodoro never logs by itself; each answer produces the right tool call."""
from __future__ import annotations

import pytest

from neuroplan import config, timer
from neuroplan.db.repository import Repository
from neuroplan.timer import PomodoroTimer, TimerCall, TimerError
from neuroplan.tools.executor import ToolExecutor

from .conftest import TODAY


@pytest.fixture
def ex(seeded: Repository) -> ToolExecutor:
    return ToolExecutor(seeded, today_fn=lambda: TODAY)


def study_block(repo: Repository):  # type: ignore[no-untyped-def]
    return next(b for b in repo.list_blocks(TODAY, TODAY) if b.block_type == config.CATCHUP)


def run_focus(t: PomodoroTimer) -> None:
    t.tick(t.remaining_seconds)


def test_focus_end_logs_nothing_until_confirmed(ex: ToolExecutor, seeded: Repository) -> None:
    block = study_block(seeded)
    events: list[str] = []
    t = PomodoroTimer(on_focus_end=lambda: events.append("focus_end"),
                      on_break_start=lambda: events.append("break_start"),
                      on_break_end=lambda: events.append("break_end"))
    before = seeded.snapshot()
    t.start(block.id, 50, 10, "Deep Learning")
    assert (t.phase, t.clock(), t.awaiting_confirmation) == (timer.FOCUS, "50:00", False)
    t.tick(49 * 60 + 59)
    assert t.phase == timer.FOCUS and t.clock() == "00:01" and events == []
    t.tick(1)
    assert events == ["focus_end", "break_start"]
    assert t.phase == timer.BREAK and t.clock() == "10:00", "focus/break switch automatically"
    assert t.awaiting_confirmation is True
    # nothing was logged: same DB state, block still pending, no actions
    assert seeded.snapshot() == before and seeded.list_actions() == []
    pending = seeded.get_block(block.id)
    assert pending is not None and pending.status == "pending"
    # even after the whole break has passed
    t.tick(10 * 60)
    assert events == ["focus_end", "break_start", "break_end"] and t.phase == timer.IDLE
    assert t.awaiting_confirmation is True and seeded.snapshot() == before


def test_done_produces_complete_block(ex: ToolExecutor, seeded: Repository) -> None:
    block = study_block(seeded)
    t = PomodoroTimer()
    t.start(block.id, block.duration_min, 10)
    run_focus(t)
    call = t.confirm_done()
    assert call == TimerCall("complete_block", {"id": block.id, "actual_min": 50, "status": "done"})
    assert t.awaiting_confirmation is False and t.block_id is None
    assert ex.execute(call.tool, call.args).ok
    done = seeded.get_block(block.id)
    assert done is not None and (done.status, done.actual_min) == ("done", 50)
    with pytest.raises(TimerError):
        t.confirm_done()


def test_partial_uses_custom_minutes(ex: ToolExecutor, seeded: Repository) -> None:
    block = study_block(seeded)
    t = PomodoroTimer()
    t.start(block.id, 50, 10)
    run_focus(t)
    assert t.elapsed_focus_minutes == 50, "the partial field is pre-filled with the elapsed focus minutes"
    call = t.confirm_partial(20)
    assert call == TimerCall("complete_block", {"id": block.id, "actual_min": 20, "status": "partial"})
    assert ex.execute(call.tool, call.args).ok
    partial = seeded.get_block(block.id)
    assert partial is not None and (partial.status, partial.actual_min) == ("partial", 20)
    unit = next(u for u in seeded.list_units(block.subject_id) if u.number == block.unit_number)
    assert unit.minutes_logged == 20


def test_partial_defaults_to_elapsed_minutes() -> None:
    t = PomodoroTimer()
    t.start(7, 50, 10)
    t.tick(17 * 60 + 20)
    t.finish_early()
    assert t.awaiting_confirmation and t.phase == timer.BREAK
    assert t.confirm_partial() == TimerCall("complete_block", {"id": 7, "actual_min": 17, "status": "partial"})


def test_did_not_study_produces_skip_block(ex: ToolExecutor, seeded: Repository) -> None:
    block = study_block(seeded)
    t = PomodoroTimer()
    t.start(block.id, 50, 10)
    run_focus(t)
    call = t.confirm_not_studied()
    assert call.tool == "skip_block" and call.args["id"] == block.id
    assert ex.execute(call.tool, call.args).ok
    skipped = seeded.get_block(block.id)
    assert skipped is not None and skipped.status == "skipped"
    unit = next(u for u in seeded.list_units(block.subject_id) if u.number == block.unit_number)
    assert unit.minutes_logged == 0


def test_pause_resume_reset() -> None:
    t = PomodoroTimer()
    t.tick(100)                                   # idle: nothing happens
    assert t.phase == timer.IDLE
    t.start(1, 25, 5)
    t.tick(60)
    t.pause()
    t.tick(600)
    assert t.clock() == "24:00" and not t.running
    t.toggle_pause()
    t.tick(60)
    assert t.clock() == "23:00" and t.running
    t.reset()
    assert (t.phase, t.block_id, t.awaiting_confirmation, t.clock()) == (timer.IDLE, None, False, "00:00")
    with pytest.raises(TimerError):
        t.confirm_partial(5)
    with pytest.raises(TimerError):
        t.finish_early()
    with pytest.raises(TimerError):
        t.start(1, 0, 5)


def test_large_tick_crosses_phases_and_done_after_early_finish() -> None:
    t = PomodoroTimer()
    t.start(3, 25, 5)
    t.tick(25 * 60 + 30)
    assert t.phase == timer.BREAK and t.clock() == "04:30" and t.elapsed_focus_seconds == 25 * 60
    t.start(4, 50, 10)                            # starting another block replaces the old one
    t.tick(30 * 60)
    t.finish_early()
    assert t.confirm_done() == TimerCall("complete_block", {"id": 4, "actual_min": 30, "status": "done"})
    no_break = PomodoroTimer()
    no_break.start(5, 25, 0)
    no_break.tick(25 * 60)
    assert no_break.phase == timer.IDLE and no_break.awaiting_confirmation
