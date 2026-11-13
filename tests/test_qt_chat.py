"""Qt coach panel: offline chat end to end, Undo, confirm card, options, RTL, anchor on resize, 500 rows."""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPoint, Qt

from neuroplan.db.repository import Repository
from neuroplan.models import ChatMessage

from .conftest import TODAY
from .qt_fixtures import close_window, make_window, wait_for, win  # noqa: F401

LONG = "This is a longer coach answer that wraps over several lines in the panel, so widths matter. " * 2
ARABIC = "لدي امتحان في الإحصاء يوم الأحد، أريد خطة مراجعة لهذا الأسبوع من فضلك"


def _fill(repo: Repository, n: int) -> None:
    for i in range(n):
        text = ARABIC if i % 7 == 3 else (LONG if i % 3 == 0 else f"short message {i}")
        repo.add_message("coach" if i % 2 else "user", text)


def test_offline_chat_flow_and_undo(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    panel = win.panel
    before = panel.view.chat_model.rowCount()
    qtbot.keyClicks(panel.input, "today I only have 90 minutes")
    qtbot.keyClick(panel.input, Qt.Key.Key_Return)
    wait_for(qtbot, lambda: panel.view.chat_model.rowCount() >= before + 2)
    assert panel.input.toPlainText() == ""
    reply = panel.items[-1]
    assert reply.role == "coach" and "Set today to 90 min" in reply.text and reply.show_undo
    assert seeded.get_overrides(TODAY)[TODAY].minutes == 90
    undo = [b for b in panel.live_buttons() if b.text() == "Undo"]
    assert len(undo) == 1
    qtbot.mouseClick(undo[0], Qt.MouseButton.LeftButton)
    wait_for(qtbot, lambda: TODAY not in seeded.get_overrides(TODAY))
    wait_for(qtbot, lambda: panel.items[-1].role == "coach" and panel.items[-1].text != reply.text)


def test_shift_enter_inserts_a_new_line(win: Any, qtbot: Any) -> None:  # noqa: F811
    panel = win.panel
    rows = panel.view.chat_model.rowCount()
    qtbot.keyClicks(panel.input, "line one")
    qtbot.keyClick(panel.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClicks(panel.input, "line two")
    assert panel.input.toPlainText() == "line one\nline two"
    assert panel.view.chat_model.rowCount() == rows


def test_confirm_card_end_to_end(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    name = seeded.list_subjects()[0].name
    result = win.call("delete_subject", {"subject": name})
    assert result.needs_confirmation
    panel = win.panel
    wait_for(qtbot, lambda: any(b.text() == "Confirm" for b in panel.live_buttons()))
    confirm = next(b for b in panel.live_buttons() if b.text() == "Confirm")
    qtbot.mouseClick(confirm, Qt.MouseButton.LeftButton)
    wait_for(qtbot, lambda: all(s.name != name for s in seeded.list_subjects()))
    # the card is retired: its buttons are no longer live
    assert not any(b.text() == "Confirm" for b in panel.live_buttons())


def test_option_buttons_reach_the_coach_and_retire(win: Any, qtbot: Any) -> None:  # noqa: F811
    panel = win.panel
    panel.add_message(ChatMessage(99100, "", "coach", "What happened?", {"options": [
        {"label": "No time", "payload": {"type": "skip_reason", "reason": "no_time", "block_id": 0}},
        {"label": "Sick", "payload": {"type": "skip_reason", "reason": "sick", "block_id": 0}}]}))
    sent: list[tuple[dict, str]] = []
    win.coach.submit_option = lambda payload, label="": sent.append((payload, label))  # type: ignore[method-assign]
    buttons = [b for b in panel.live_buttons() if b.text() in ("No time", "Sick")]
    assert len(buttons) == 2 and all(b.isEnabled() for b in buttons)
    qtbot.mouseClick(buttons[1], Qt.MouseButton.LeftButton)
    assert sent and sent[0][1] == "Sick" and sent[0][0]["reason"] == "sick"
    assert not any(b.text() in ("No time", "Sick") for b in panel.live_buttons())


def test_arabic_message_is_right_to_left_and_right_aligned(win: Any) -> None:  # noqa: F811
    from neuroplan.ui_qt.chat import PAD_X, Item, layout_message
    win.panel.add_message(ChatMessage(99200, "", "coach", ARABIC))
    item = win.panel.items[-1]
    assert item.rtl
    layout = layout_message(item, 360)
    text = next(e for e in layout.elements if e.kind == "text")
    assert abs(text.rect.right() - (layout.bubble.right() - PAD_X)) <= 1, "Arabic text ends at the right edge"
    english = Item.from_message(ChatMessage(1, "", "coach", "Plain English text"))
    assert not english.rtl
    layout = layout_message(english, 360)
    text = next(e for e in layout.elements if e.kind == "text")
    assert text.rect.left() == layout.bubble.left() + PAD_X


def _top_row(view: Any) -> tuple[int, int]:
    index = view.indexAt(QPoint(10, 1))
    return index.row(), view.visualRect(index).top()


def test_chat_keeps_its_anchor_on_resize(seeded: Repository, qtbot: Any) -> None:
    _fill(seeded, 200)
    window = make_window(seeded, qtbot)
    try:
        window.resize(1280, 800)
        qtbot.wait(50)
        view = window.panel.view
        assert view.at_bottom()
        total = sum(window.splitter.sizes())
        for width in (600, 320, 700, 380):              # bottom stays bottom
            window.splitter.setSizes([total - width, width])
            qtbot.wait(30)
            assert view.at_bottom(), width
        model = view.chat_model
        view.scrollTo(model.index(90), view.ScrollHint.PositionAtTop)
        qtbot.wait(20)
        row, top = _top_row(view)
        assert 85 <= row <= 95
        for width in (650, 320, 500):                    # the row at the top edge stays there
            window.splitter.setSizes([total - width, width])
            qtbot.wait(30)
            assert _top_row(view)[0] == row and abs(_top_row(view)[1] - top) <= 2, width
        qtbot.wait(300)                                  # idle refinement of off-screen rows keeps it too
        assert _top_row(view)[0] == row and abs(_top_row(view)[1] - top) <= 2
        assert not view.delegate.stale
    finally:
        close_window(window)


def test_500_messages_load_fast_and_new_ones_stick_to_the_bottom(seeded: Repository, qtbot: Any) -> None:
    _fill(seeded, 520)
    start = time.perf_counter()
    window = make_window(seeded, qtbot)
    elapsed = time.perf_counter() - start
    try:
        view = window.panel.view
        assert view.chat_model.rowCount() == 500 and window.panel.has_older
        assert elapsed < 3.0
        qtbot.wait(50)
        assert view.at_bottom()
        window.panel.add_message(ChatMessage(99300, "", "coach", "new message at the end"))
        qtbot.wait(20)
        assert view.at_bottom(), "sticks to the bottom when it was at the bottom"
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
        qtbot.wait(20)
        before = _top_row(view)
        window.panel.add_message(ChatMessage(99301, "", "user", "another"))
        qtbot.wait(20)
        assert _top_row(view) == before, "no jump while reading older messages"
        view.verticalScrollBar().setValue(0)             # top: the next page of older messages loads
        wait_for(qtbot, lambda: view.chat_model.rowCount() > 502)
    finally:
        close_window(window)
