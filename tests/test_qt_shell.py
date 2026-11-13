"""Qt shell: views, splitter persistence and reset, theme and text size, window geometry, coach collapse."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from neuroplan import config
from neuroplan.db.repository import Repository
from neuroplan.palette import THEMES

from .qt_fixtures import close_window, make_window, win  # noqa: F401


def test_every_view_builds_and_shows(win: Any) -> None:  # noqa: F811
    from neuroplan.ui_qt.app import VIEW_NAMES
    for name in VIEW_NAMES:
        win.show_view(name)
        assert win.current == name and win.stack.currentWidget() is win.views[name]
        assert win.nav[name].isChecked()
    assert win.splitter.opaqueResize() and not win.splitter.childrenCollapsible()


def test_splitter_state_persists_and_double_click_resets(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    total = sum(win.splitter.sizes())
    win.splitter.setSizes([total - 470, 470])
    win.save_layout()
    assert seeded.get_setting("splitter_main")
    close_window(win)
    again = make_window(seeded, qtbot)
    try:
        assert abs(again.splitter.sizes()[1] - 470) <= 2
        handle = again.splitter.handle(1)
        qtbot.mouseDClick(handle, Qt.MouseButton.LeftButton, pos=QPoint(2, 20))
        assert abs(again.splitter.sizes()[1] - config.COACH_DEFAULT_WIDTH) <= 2
    finally:
        close_window(again)


def test_window_geometry_is_remembered(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    win.resize(1300, 760)
    qtbot.wait(20)
    win.save_layout()
    close_window(win)
    again = make_window(seeded, qtbot)
    try:
        assert (again.width(), again.height()) == (1300, 760)
    finally:
        close_window(again)


def test_coach_panel_never_narrower_than_its_minimum(win: Any) -> None:  # noqa: F811
    total = sum(win.splitter.sizes())
    win.splitter.setSizes([total - 50, 50])
    assert win.splitter.sizes()[1] >= config.COACH_MIN_WIDTH
    win.splitter.setSizes([10, total - 10])
    assert win.splitter.sizes()[0] >= win.centre.minimumSizeHint().width()


def _window_color(widget: Any) -> str:
    from PySide6.QtGui import QPalette
    return widget.palette().color(QPalette.ColorRole.Window).name().upper()


def test_theme_switch_applies_live_and_is_stored(win: Any, seeded: Repository) -> None:  # noqa: F811
    from neuroplan.ui_qt.style import LOOK
    app = QApplication.instance()
    assert app.styleSheet() == "" and app.style().name().lower() == "fusion", "no stylesheet (slow)"
    assert _window_color(app) == THEMES["Midnight"].colors["bg"]
    title = win.views["Today"].title_label
    win.cycle_theme()
    assert LOOK.theme == "Ivory & Gold" and seeded.get_setting("theme") == "Ivory & Gold"
    assert _window_color(app) == THEMES["Ivory & Gold"].colors["bg"]
    assert _window_color(win.views["Today"]) == THEMES["Ivory & Gold"].colors["bg"]
    from PySide6.QtGui import QPalette
    assert title.palette().color(QPalette.ColorRole.WindowText).name().upper() == THEMES["Ivory & Gold"].colors["text"]
    assert "Ivory & Gold" in win.theme_button.text()
    win.set_theme("Mist")
    assert _window_color(win.panel) == THEMES["Mist"].colors["surface"]
    win.set_theme("Midnight")


def test_text_size_shortcuts(win: Any, seeded: Repository, qtbot: Any) -> None:  # noqa: F811
    from neuroplan.ui_qt.style import LOOK
    title = win.views["Today"].title_label
    assert LOOK.px(config.CHAT_FONT_SIZE) == 15 and title.font().pixelSize() == 22
    qtbot.keyClick(win, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)       # Ctrl+=  (Ctrl+Plus)
    assert LOOK.text_size == "Large" and LOOK.px(15) == 17 and LOOK.px(13) == 15
    assert seeded.get_setting("text_size") == "Large"
    assert title.font().pixelSize() == round(22 * 17 / 15), "live: existing labels get the new size"
    assert win.nav["Today"].font().pixelSize() == 15
    qtbot.keyClick(win, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(win, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert LOOK.text_size == "Small" and LOOK.px(15) == 13
    qtbot.keyClick(win, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert LOOK.text_size == "Medium" and title.font().pixelSize() == 22


def test_coach_collapses_to_a_strip_with_unread_badge(win: Any, qtbot: Any) -> None:  # noqa: F811
    from neuroplan.models import ChatMessage
    width = win.splitter.sizes()[1]
    win.panel.toggle()
    assert win.panel.collapsed and win.panel.width() <= 60
    win.panel.add_message(ChatMessage(99001, "", "coach", "A proactive hello"))
    assert win.panel.unread == 1 and win.panel.badge.isVisible() and win.panel.badge.text() == "1"
    assert win.panel.strip_button.toolTip()
    win.panel.toggle()
    assert not win.panel.collapsed and win.panel.unread == 0 and not win.panel.badge.isVisible()
    assert abs(win.splitter.sizes()[1] - width) <= 2
