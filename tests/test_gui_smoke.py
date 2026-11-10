"""GUI smoke test: build the app on a temp DB, visit every view, chat offline, destroy.
Skipped automatically when no display is available."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

import pytest

from neuroplan import timer as timer_mod
from neuroplan.db.repository import Repository

from .conftest import TODAY


def _display_available() -> bool:
    try:
        import tkinter
        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _display_available(), reason="no display available")


def pump(app, seconds: float = 0.0, until=None) -> None:  # type: ignore[no-untyped-def]
    """Run the Tk event loop by hand (no mainloop in tests)."""
    deadline = time.time() + max(seconds, 0.05 if until is None else 5.0)
    while time.time() < deadline:
        app.update()
        app.process_ui_queue()
        if until is not None and until():
            break
        time.sleep(0.01)
    app.update()
    app.process_ui_queue()


@pytest.fixture
def app(seeded: Repository) -> Iterator[object]:
    from neuroplan.ui.app import NeuroPlanApp
    instance = NeuroPlanApp(seeded, today_fn=lambda: TODAY, run_startup=False)
    instance.withdraw()
    yield instance
    try:
        instance.destroy()
    except Exception:
        pass


def test_gui_smoke(app, seeded: Repository, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from neuroplan.ui.app import VIEW_NAMES
    pump(app)
    assert not app.coach.llm_enabled(), "the smoke test runs with the offline coach"
    for name in VIEW_NAMES:
        app.show_view(name)
        pump(app)
        assert app.current == name
    # one offline chat message through the real worker thread
    before = len(seeded.list_messages())
    app.panel.input.insert("1.0", "today I only have 90 minutes")
    app.panel.send()
    pump(app, until=lambda: len(seeded.list_messages()) >= before + 2
         and seeded.list_messages()[-1].role == "coach")
    last = seeded.list_messages()[-1]
    assert last.role == "coach" and "Set today to 90 min" in last.text
    assert seeded.get_overrides(TODAY)[TODAY].minutes == 90
    pump(app, 0.2)
    assert app.panel._rendered >= before + 2
    # views refresh on state_changed and UI actions go through the executor
    app.show_view("Today")
    pump(app)
    block = next(b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "pending")
    assert app.call("complete_block", {"id": block.id}).ok
    pump(app, 0.2)
    assert seeded.list_actions(1)[0].source == "ui"
    # startup posts the proactive greeting
    app.coach.startup()
    pump(app, until=lambda: any("Today (Tuesday)" in m.text for m in seeded.list_messages()))
    assert any(m.proactive and "Today (Tuesday)" in m.text for m in seeded.list_messages())
    # collapse → unread badge, expand again
    app.panel.toggle()
    app.bus.emit("break_started")
    pump(app, until=lambda: app.panel.unread >= 1)
    assert app.panel.collapsed and app.panel.unread >= 1
    app.panel.toggle()
    assert app.panel.unread == 0
    # export through the settings view
    target = tmp_path / "export.json"
    assert app.views["Settings"].export_to(str(target))
    assert json.loads(target.read_text(encoding="utf-8"))["format"] == "neuroplan-export-1"


def test_gui_timer_confirmation_flow(app, seeded: Repository) -> None:  # type: ignore[no-untyped-def]
    app.show_view("Today")
    pump(app)
    plan = app.read("get_plan")["days"][0]["blocks"]
    block = next(b for b in plan if b["status"] == "pending" and b["type"] != "REVIEW")
    today = app.views["Today"]
    today._start(block)
    locked = seeded.get_block(block["id"])
    assert locked is not None and locked.locked, "a started block is protected from re-planning"
    assert app.timer.phase == timer_mod.FOCUS
    app.timer.tick(block["min"] * 60)
    pump(app)
    assert app.timer.awaiting_confirmation and today.pomodoro._confirm_visible
    still = seeded.get_block(block["id"])
    assert still is not None and still.status == "pending", "nothing is logged before the student answers"
    today.pomodoro.partial_var.set("20")
    today.pomodoro._partial()
    pump(app, 0.1)
    logged = seeded.get_block(block["id"])
    assert logged is not None and (logged.status, logged.actual_min) == ("partial", 20)
    assert not today.pomodoro._confirm_visible


def test_gui_confirmation_card_for_delete(app, seeded: Repository) -> None:  # type: ignore[no-untyped-def]
    app.show_view("Subjects")
    pump(app)
    subjects = app.views["Subjects"]
    subjects._select("IELTS Prep")
    pump(app)
    subjects._delete()
    pump(app, 0.1)
    card = seeded.list_messages()[-1]
    assert card.actions["confirmations"][0]["tool"] == "delete_subject"
    assert seeded.get_subject_by_name("IELTS Prep") is not None
    app.coach.select_option({"type": "confirm", **card.actions["confirmations"][0]}, "Confirm")
    pump(app, 0.1)
    assert seeded.get_subject_by_name("IELTS Prep") is None
    # new subject through the form
    subjects._new()
    subjects.v["name"].set("Databases")
    subjects.v["total_units"].set("8")
    subjects._save()
    pump(app, 0.1)
    created = seeded.get_subject_by_name("Databases")
    assert created is not None and len(seeded.list_units(created.id)) == 8
