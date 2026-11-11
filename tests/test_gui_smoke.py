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


def test_gui_settings_clean_pasted_values_and_show_the_real_test_result(  # type: ignore[no-untyped-def]
        app, seeded: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    from neuroplan.coach import coach as coach_module

    from .test_provider_errors import FREE_TIER, GEMINI_URL, answering, gemini_error, provider_for
    denied = answering(403, gemini_error(403, FREE_TIER, "PERMISSION_DENIED"))       # mock transport, no network
    monkeypatch.setattr(coach_module, "build_provider", lambda settings: (provider_for(denied), ""))
    app.show_view("Settings")
    pump(app)
    view = app.views["Settings"]
    view.v["provider"].set("openai_compat")
    view.v["base_url"].set(f' "{GEMINI_URL}models" ')
    view.v["chat_model"].set("'gemini-flash-latest'")
    view._test()                                        # saves first, then tests on a worker thread
    assert seeded.get_setting("base_url") == GEMINI_URL.rstrip("/")
    assert view.v["base_url"].get() == GEMINI_URL.rstrip("/"), "the field shows the cleaned value"
    assert view.v["chat_model"].get() == seeded.get_setting("chat_model") == "gemini-flash-latest"
    pump(app, until=lambda: view.key_status.cget("text") not in ("", "Testing…"))
    shown = view.key_status.cget("text")
    assert "denied access (HTTP 403)" in shown and "free tier is not available for this project" in shown
    assert "API key was rejected" not in shown and view.test_button.cget("state") == "normal"


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


def test_gui_archive_restore_and_delete_buttons(app, seeded: Repository) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta
    app.show_view("Subjects")
    pump(app)
    view = app.views["Subjects"]
    week = lambda: [b for b in seeded.list_blocks(TODAY, TODAY + timedelta(days=6))  # noqa: E731
                    if b.status == "pending"]
    ielts = seeded.get_subject_by_name("IELTS Prep")
    assert ielts is not None and any(b.subject_id == ielts.id for b in week())
    assert ("IELTS Prep", "archive") in view.row_buttons and ("IELTS Prep", "delete") in view.row_buttons
    # Archive: active=0, the plan is rebuilt without it, the row disappears, history stays
    units = len(seeded.list_units(ielts.id))
    view.row_buttons[("IELTS Prep", "archive")].invoke()
    pump(app, 0.1)
    archived = seeded.get_subject_by_name("IELTS Prep")
    assert archived is not None and not archived.active and len(seeded.list_units(ielts.id)) == units
    assert week() and all(b.subject_id != ielts.id for b in week())
    assert seeded.list_actions(1)[0].source == "ui", "the button goes through the tool executor"
    assert [s["name"] for s in view.visible_subjects()] == ["Deep Learning", "Machine Learning",
                                                           "Internet Programming"]
    assert not any(name == "IELTS Prep" for name, _kind in view.row_buttons)
    assert view.archived_switch.cget("text") == "Show archived (1)"
    # Show archived -> Restore
    view.archived_switch.toggle()
    pump(app, 0.1)
    assert view.show_archived.get() and ("IELTS Prep", "restore") in view.row_buttons
    view.row_buttons[("IELTS Prep", "restore")].invoke()
    pump(app, 0.1)
    restored = seeded.get_subject_by_name("IELTS Prep")
    assert restored is not None and restored.active
    assert any(b.subject_id == ielts.id for b in week())
    assert ("IELTS Prep", "archive") in view.row_buttons
    # both steps can be undone
    assert app.call("undo_last").ok and not seeded.get_subject_by_name("IELTS Prep").active
    assert app.call("undo_last").ok and seeded.get_subject_by_name("IELTS Prep").active
    pump(app, 0.1)
    # Delete: confirmation card first, then the subject is gone; undo brings it back
    view.row_buttons[("IELTS Prep", "delete")].invoke()
    pump(app, 0.1)
    card = seeded.list_messages()[-1]
    assert card.actions["confirmations"][0]["tool"] == "delete_subject"
    assert seeded.get_subject_by_name("IELTS Prep") is not None, "nothing is deleted before Confirm"
    app.coach.select_option({"type": "confirm", **card.actions["confirmations"][0]}, "Confirm")
    pump(app, 0.1)
    assert seeded.get_subject_by_name("IELTS Prep") is None
    assert all(b.subject_id != ielts.id for b in week())
    app.coach.select_option({"type": "undo"}, "Undo")
    pump(app, 0.1)
    assert seeded.get_subject_by_name("IELTS Prep") is not None
    # archiving the subject that is open in the form: it stays while archived ones are shown ...
    view._select("Deep Learning")
    pump(app)
    view.row_buttons[("Deep Learning", "archive")].invoke()
    pump(app, 0.1)
    assert not seeded.get_subject_by_name("Deep Learning").active
    assert view.selected == "Deep Learning" and ("Deep Learning", "restore") in view.row_buttons
    view.archived_switch.toggle()                     # ... and the form is cleared once they are hidden
    pump(app, 0.1)
    assert not view.show_archived.get() and "Deep Learning" not in [s["name"] for s in view.visible_subjects()]
    assert view.selected is None and view.delete_button.cget("state") == "disabled"
    # every row fits the table: the buttons are not pushed out of view
    table = view.table._parent_canvas
    assert all(row.winfo_children()[-1].winfo_reqwidth() <= table.winfo_reqwidth() for row in
               view.table.winfo_children())


def test_gui_chat_bubbles_wrap_to_panel_width(app, seeded: Repository) -> None:  # type: ignore[no-untyped-def]
    import customtkinter as ctk

    from neuroplan.models import ChatMessage
    app.deiconify()                                   # sizes are only real on a mapped window
    pump(app, 0.3)
    panel = app.panel
    canvas = panel.messages._parent_canvas
    scale = ctk.ScalingTracker.get_widget_scaling(panel)
    long_word = "Pneumonoultramicroscopicsilicovolcanoconiosis" * 4
    sentence = "This is a long coach answer that has to wrap inside the bubble instead of running off the panel. "
    summary = "Machine Learning: 7 expected by now → 3 unit(s) behind, the week was re-planned around it " * 2
    panel.add_message(ChatMessage(9001, "", "user", sentence * 3 + long_word))
    panel.add_message(ChatMessage(9002, "", "coach", sentence * 4 + long_word, {
        "actions": [{"tool": "set_day_override", "ok": True, "summary": summary, "action_id": 1, "write": True},
                    {"tool": "set_progress", "ok": False, "summary": long_word, "action_id": None, "write": True}],
        "confirmations": [{"tool": "delete_subject", "args": {}, "preview": sentence * 2 + long_word}]}))
    panel.add_message(ChatMessage(9003, "", "system_event", sentence * 3))
    pump(app, 0.3)

    def assert_fits() -> None:
        width = canvas.winfo_width()
        assert abs(panel._area - int(width / scale)) <= 1, "the wrap width follows the message list"
        for holder in panel.messages.winfo_children():
            assert holder.winfo_reqwidth() <= width, "a message is wider than the panel (clipped text)"
        for text, inset in panel._wrapped:
            assert text.cget("wraplength") == panel._area - inset
            assert text.winfo_reqwidth() <= text.cget("wraplength") * scale + 4, "long words must break too"

    assert len(panel._wrapped) >= 6 and any(inset for _t, inset in panel._wrapped)
    assert_fits()
    before = panel._area
    lines = max(text.winfo_reqheight() for text, _inset in panel._wrapped)
    panel.configure(width=560)                        # a wider panel: every text re-wraps to the new width
    pump(app, 0.3)
    assert panel._area > before + 100
    assert_fits()
    assert max(text.winfo_reqheight() for text, _inset in panel._wrapped) < lines, "wider panel, fewer lines"
    panel.configure(width=300)                        # and a narrower one
    pump(app, 0.3)
    assert panel._area < before
    assert_fits()
    panel.load_history()                              # re-rendered history uses the current width
    pump(app, 0.2)
    assert panel._wrapped
    assert_fits()


def test_gui_proactive_wiring(app, seeded: Repository) -> None:  # type: ignore[no-untyped-def]
    """Timer breaks, the end-of-day clock and the close button all reach the coach through events."""
    from datetime import datetime
    app.show_view("Today")
    pump(app)
    texts = lambda: [m.text for m in seeded.list_messages() if m.role == "coach"]  # noqa: E731
    # pomodoro break -> break_started -> local tip (no LLM)
    plan = app.read("get_plan")["days"][0]["blocks"]
    block = next(b for b in plan if b["status"] == "pending" and b["type"] != "REVIEW")
    app.views["Today"]._start(block)
    app.timer.tick(block["min"] * 60)
    pump(app, until=lambda: any(t.startswith("Break") for t in texts()))
    assert any(t.startswith("Break") for t in texts())
    # Done on the card -> block_finished one-liner
    app.views["Today"].pomodoro._done()
    pump(app, until=lambda: any(t.startswith("✓ ") for t in texts()))
    assert any(t.startswith("✓ ") and "done (50 min)" in t for t in texts())
    # skipping from the timeline -> "what happened?" with quick chips
    other = next(b for b in seeded.list_blocks(TODAY, TODAY) if b.status == "pending")
    assert app.call("skip_block", {"id": other.id}).ok
    pump(app, until=lambda: any("What happened?" in t for t in texts()))
    ask = next(m for m in seeded.list_messages() if "What happened?" in m.text)
    assert len(ask.actions["options"]) == 4
    # minute timer -> end_of_day once the configured time has passed
    app.coach.now = lambda: datetime(2026, 11, 10, 22, 5)
    app._minute()
    pump(app, until=lambda: any("End-of-day check-in" in t for t in texts()))
    assert sum("End-of-day check-in" in t for t in texts()) == 1
    # closing without a check-in today posts it and keeps the window open once
    seeded.set_setting("last_checkin_date", "")
    app._on_close()
    pump(app, until=lambda: sum("End-of-day check-in" in t for t in texts()) == 2)
    assert app.winfo_exists() and sum("End-of-day check-in" in t for t in texts()) == 2
    app._on_close()
    assert app._closing
