"""Qt Week and Settings views: buttons → tools, board, banner, settings round trip, test connection."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt

from neuroplan import config
from neuroplan.db.repository import Repository

from .conftest import TODAY
from .qt_fixtures import wait_for, win  # noqa: F401
from .test_qt_today import _spy


def test_week_board_and_buttons(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    from neuroplan.ui_qt.views.week import BlockChip
    win.show_view("Week")
    view = win.views["Week"]
    assert view.board.count() == 7 and len(view.rows) == 7
    chips = view.board_widget.findChildren(BlockChip)
    from datetime import timedelta
    start = view._week_start(TODAY)
    week = win.read("get_plan", {"date_from": start.isoformat(), "date_to": (start + timedelta(days=6)).isoformat()})
    planned = sum(len(d["blocks"]) for d in week["days"])
    assert len(chips) == planned and all(c.toolTip() for c in chips)
    calls = _spy(win)
    spin, start, peak = view.rows[TODAY.weekday()]
    spin.setValue(150)
    qtbot.mouseClick(view.save_week_button, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "set_weekly_availability" and len(calls[-1][1]["days"]) == 7
    assert seeded.get_availability()[TODAY.weekday()].minutes == 150
    wait_for(qtbot, lambda: TODAY.weekday() in view.rows)
    view.override.setValue(45)
    qtbot.mouseClick(view.apply_button, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("set_day_override", {"date": TODAY.isoformat(), "minutes": 45})
    wait_for(qtbot, lambda: "override active" in view.override_note.text())
    qtbot.mouseClick(view.clear_button, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("set_day_override", {"date": TODAY.isoformat(), "clear": True})
    qtbot.mouseClick(view.rebalance_button, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("regenerate_plan", {})
    asked: list[str] = []
    win.coach.submit = lambda text: asked.append(text)  # type: ignore[method-assign]
    qtbot.mouseClick(view.why_button, Qt.MouseButton.LeftButton)
    assert asked == ["Why this order?"]


def test_week_risk_banner(win: Any, monkeypatch: Any) -> None:  # noqa: F811
    view = win.views["Week"]
    real = win.read

    def read(tool: str, args: Any = None) -> Any:
        if tool == "get_risks":
            return {"risks": [{"title": "Midterm", "date": "2026-11-20", "shortfall_min": 90}]}
        return real(tool, args)
    monkeypatch.setattr(win, "read", read)
    win.show_view("Week")
    assert view.banner.isVisible() and "90 min short" in view.banner_text.text()


def test_settings_round_trip_and_live_look(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    from neuroplan.ui_qt.style import LOOK
    win.show_view("Settings")
    view = win.views["Settings"]
    values = view.values()
    assert set(values) == set(config.EDITABLE_SETTINGS)
    assert values["provider"] == "offline" and "needs no key" in view.key_status.text()
    calls = _spy(win)
    view.setters["plan_horizon_days"]("21")
    view.setters["base_url"](' "http://localhost:11434/v1/chat/completions" ')
    qtbot.mouseClick(view.save_button, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "update_settings" and set(calls[-1][1]["settings"]) == set(config.EDITABLE_SETTINGS)
    assert seeded.get_setting("plan_horizon_days") == "21"
    assert view.getters["base_url"]() == "http://localhost:11434/v1", "shown as cleaned and stored"
    assert view.save_status.text() == "Saved."
    view.theme.textActivated.emit("Mist")                 # picking a theme applies it at once
    assert LOOK.theme == "Mist" and seeded.get_setting("theme") == "Mist"
    view.text_size.textActivated.emit("Large")
    assert LOOK.text_size == "Large"
    win.set_theme("Midnight")
    win.set_text_size("Medium")


def test_test_connection_reports_the_real_reason(win: Any, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Settings")
    view = win.views["Settings"]

    def fake(done: Any) -> None:            # runs on a worker thread like the real one
        threading.Thread(target=lambda: done(False, "Access denied (HTTP 403): free tier not available")).start()
    win.coach.test_connection_async = fake  # type: ignore[method-assign]
    qtbot.mouseClick(view.test_button, Qt.MouseButton.LeftButton)
    assert not view.test_button.isEnabled()
    wait_for(qtbot, lambda: "HTTP 403" in view.key_status.text())
    assert view.test_button.isEnabled() and view.key_status.property("tone") == "danger"


def test_settings_data_tools(win: Any, tmp_path: Path, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Settings")
    view = win.views["Settings"]
    target = tmp_path / "export.json"
    assert view.export_to(str(target))
    assert json.loads(target.read_text(encoding="utf-8"))["format"] == "neuroplan-export-1"
    qtbot.mouseClick(view.reset_button, Qt.MouseButton.LeftButton)
    wait_for(qtbot, lambda: any(b.text() == "Confirm" for b in win.panel.live_buttons()))
