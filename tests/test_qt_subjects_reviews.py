"""Qt Subjects and Reviews views: buttons → tools, form round trip, archive filter, splitter state."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt

from neuroplan import config
from neuroplan.db.repository import Repository

from .conftest import TODAY
from .qt_fixtures import close_window, make_window, wait_for, win  # noqa: F401
from .test_qt_today import _spy


def test_subject_cards_and_form_round_trip(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Subjects")
    view = win.views["Subjects"]
    names = [s.name for s in seeded.list_subjects(active_only=True)]
    assert set(view.cards) == set(names)
    first = names[0]
    qtbot.mouseClick(view.cards[first], Qt.MouseButton.LeftButton, pos=QPoint(20, 12))
    assert view.selected == first and view.name_edit.text() == first and view.delete_button.isEnabled()
    assert view.cards[first].property("card") == "selected"
    calls = _spy(win)
    view.total_units.setValue(view.total_units.value() + 2)
    qtbot.mouseClick(view.save_button, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "upsert_subject" and calls[-1][1]["name"] == first
    subject = next(s for s in seeded.list_subjects() if s.name == first)
    assert len(seeded.list_units(subject.id)) == calls[-1][1]["total_units"]
    # a new subject
    qtbot.mouseClick(view.new_button, Qt.MouseButton.LeftButton)
    assert view.selected is None and not view.delete_button.isEnabled()
    view.name_edit.setText("Organic Chemistry")
    qtbot.mouseClick(view.save_button, Qt.MouseButton.LeftButton)
    assert any(s.name == "Organic Chemistry" for s in seeded.list_subjects())
    assert view.selected == "Organic Chemistry"


def test_archive_restore_delete_buttons(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Subjects")
    view = win.views["Subjects"]
    calls = _spy(win)
    name = next(iter(view.cards))
    qtbot.mouseClick(view.cards[name].toggle, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("upsert_subject", {"name": name, "active": False})
    wait_for(qtbot, lambda: name not in view.cards)
    view.show_archived.setChecked(True)
    assert name in view.cards and view.cards[name].toggle.text() == "Restore"
    qtbot.mouseClick(view.cards[name].toggle, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("upsert_subject", {"name": name, "active": True})
    wait_for(qtbot, lambda: name in view.cards and view.cards[name].toggle.text() == "Archive")
    qtbot.mouseClick(view.cards[name].delete, Qt.MouseButton.LeftButton)
    assert calls[-1] == ("delete_subject", {"subject": name})
    wait_for(qtbot, lambda: any(b.text() == "Confirm" for b in win.panel.live_buttons()))


def test_assessments_add_edit_delete(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Subjects")
    view = win.views["Subjects"]
    name = next(iter(view.cards))
    view.select(name)
    subject = next(s for s in seeded.list_subjects() if s.name == name)
    before = len(seeded.list_assessments(subject.id))
    calls = _spy(win)
    view.assess_title_edit.setText("Quiz 3")
    view.weight.setValue(10)
    qtbot.mouseClick(view.assess_save, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "add_assessment" and calls[-1][1]["title"] == "Quiz 3"
    assert calls[-1][1]["date"] == TODAY.isoformat() and "unit_from" not in calls[-1][1]
    assert len(seeded.list_assessments(subject.id)) == before + 1
    added = next(a for a in seeded.list_assessments(subject.id) if a.title == "Quiz 3")
    wait_for(qtbot, lambda: view.assess_list.count() >= before + 1)
    view.edit_assessment({"id": added.id, "kind": added.kind, "date": added.date.isoformat(), "title": "Quiz 3b",
                          "weight_pct": 15, "unit_from": 1, "unit_to": 2})
    assert view.assess_save.text() == "Update assessment"
    qtbot.mouseClick(view.assess_save, Qt.MouseButton.LeftButton)
    assert calls[-1][0] == "update_assessment" and calls[-1][1]["id"] == added.id
    assert next(a for a in seeded.list_assessments(subject.id) if a.id == added.id).title == "Quiz 3b"


def test_subjects_splitter_state_persists_and_resets(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Subjects")
    view = win.views["Subjects"]
    qtbot.wait(20)
    total = sum(view.splitter.sizes())
    view.splitter.setSizes([330, total - 330])
    view._save_splitter()
    assert view.splitter.sizes()[0] >= config.COLUMN_MIN_WIDTH
    close_window(win)
    again = make_window(seeded, qtbot)
    try:
        again.show_view("Subjects")
        qtbot.wait(20)
        split = again.views["Subjects"].splitter
        assert abs(split.sizes()[0] - 330) <= 2
        qtbot.mouseDClick(split.handle(1), Qt.MouseButton.LeftButton, pos=QPoint(2, 20))
        room = sum(split.sizes())             # the form keeps its minimum width
        expected = min(config.SUBJECTS_LIST_DEFAULT_WIDTH, room - config.COLUMN_MIN_WIDTH)
        assert abs(split.sizes()[0] - expected) <= 2
        split.setSizes([50, total - 50])
        assert split.sizes()[0] >= config.COLUMN_MIN_WIDTH
    finally:
        close_window(again)


def test_review_rating_buttons(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    win.show_view("Reviews")
    view = win.views["Reviews"]
    assert view.rating_buttons, "the seed has reviews due"
    review_id, buttons = next(iter(view.rating_buttons.items()))
    assert list(buttons) == list(config.RATINGS) and all(b.toolTip() for b in buttons.values())
    calls = _spy(win)
    qtbot.mouseClick(buttons["good"], Qt.MouseButton.LeftButton)
    assert calls[-1] == ("complete_review", {"id": review_id, "rating": "good"})
    wait_for(qtbot, lambda: review_id not in view.rating_buttons)
