"""Qt Today view: KPI cards, timeline buttons → tools, pomodoro confirmation flow."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from neuroplan import timer as timer_mod
from neuroplan.db.repository import Repository

from .conftest import TODAY
from .qt_fixtures import win  # noqa: F401


def _spy(win: Any) -> list[tuple[str, dict]]:  # noqa: F811
    calls: list[tuple[str, dict]] = []
    real = win.call

    def call(tool: str, args: Any = None) -> Any:
        calls.append((tool, dict(args or {})))
        return real(tool, args)
    win.call = call
    return calls


def _pending(seeded: Repository) -> list[Any]:
    return [b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "pending"]


def test_today_shows_kpis_and_the_timeline(win: Any, seeded: Repository) -> None:  # noqa: F811
    view = win.views["Today"]
    win.show_view("Today")
    assert "min available" in view.subtitle.text()
    assert view.kpi_backlog.value.text().endswith("units")
    assert set(view.block_rows) >= {b.id for b in _pending(seeded)}
    some = next(iter(view.block_rows.values()))
    assert set(some) == {"start", "done", "skip", "lock"} and some["start"].toolTip()


def test_timeline_buttons_reach_the_right_tools(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    view = win.views["Today"]
    win.show_view("Today")
    calls = _spy(win)
    first, second, third = _pending(seeded)[:3]
    qtbot.mouseClick(view.block_rows[first.id]["done"], Qt.MouseButton.LeftButton)
    assert calls[-1] == ("complete_block", {"id": first.id})
    qtbot.waitUntil(lambda: second.id in view.block_rows and "skip" in view.block_rows[second.id])
    qtbot.mouseClick(view.block_rows[second.id]["skip"], Qt.MouseButton.LeftButton)
    assert calls[-1] == ("skip_block", {"id": second.id})
    qtbot.waitUntil(lambda: third.id in view.block_rows and "lock" in view.block_rows[third.id])
    qtbot.mouseClick(view.block_rows[third.id]["lock"], Qt.MouseButton.LeftButton)
    assert calls[-1] == ("lock_block", {"id": third.id, "locked": True})
    statuses = {b.id: b.status for b in seeded.list_blocks(TODAY, TODAY)}
    assert statuses[first.id] == "done" and statuses[second.id] == "skipped"


def test_pomodoro_never_logs_before_the_answer(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    view = win.views["Today"]
    win.show_view("Today")
    calls = _spy(win)
    block = next(b for b in _pending(seeded) if b.block_type != "REVIEW")
    qtbot.mouseClick(view.block_rows[block.id]["start"], Qt.MouseButton.LeftButton)
    assert calls[-1] == ("lock_block", {"id": block.id, "locked": True})
    assert win.timer.phase == timer_mod.FOCUS and view.pomodoro.pause_button.isEnabled()
    win.timer.tick(block.duration_min * 60)                      # focus time is over
    view.pomodoro.refresh()
    assert view.pomodoro.confirm.isVisible()
    assert not any(tool == "complete_block" for tool, _ in calls), "nothing logged before the answer"
    view.pomodoro.partial_minutes.setValue(20)
    qtbot.mouseClick(view.pomodoro.partial_button, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "complete_block" and calls[-1][1]["status"] == "partial"
    assert calls[-1][1]["actual_min"] == 20
    assert not view.pomodoro.confirm.isVisible()


def test_pomodoro_done_and_not_studied(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    view = win.views["Today"]
    win.show_view("Today")
    calls = _spy(win)
    first, second = [b for b in _pending(seeded) if b.block_type != "REVIEW"][:2]
    view._start({"id": first.id, "locked": False, "subject": "S", "type": first.block_type,
                 "unit": first.unit_number, "min": first.duration_min, "pomodoro": first.pomodoro})
    win.timer.tick(first.duration_min * 60)
    view.pomodoro.refresh()
    qtbot.mouseClick(view.pomodoro.done_button, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "complete_block" and calls[-1][1]["id"] == first.id
    win.timer.reset()
    view._start({"id": second.id, "locked": False, "subject": "S", "type": second.block_type,
                 "unit": second.unit_number, "min": second.duration_min, "pomodoro": second.pomodoro})
    win.timer.tick(second.duration_min * 60)
    view.pomodoro.refresh()
    qtbot.mouseClick(view.pomodoro.not_studied_button, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("skip_block", {"id": second.id, "reason": "Didn't really study"})
