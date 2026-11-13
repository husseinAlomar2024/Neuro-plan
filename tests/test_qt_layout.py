"""Qt layout limits: every view fits a 1024x700 window with the coach panel at its minimum width, and no
widget is clipped or squeezed below its own minimum width in any view, theme or window size."""
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QWidget

from neuroplan import config

from .qt_fixtures import win  # noqa: F401


def clipped(root: QWidget) -> list[str]:
    """Visible widgets under ``root`` that stick out of their parent or are narrower than they need."""
    problems: list[str] = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisible() or widget.parentWidget() is None:
            continue
        parent = widget.parentWidget()
        if widget.x() < -1 or widget.x() + widget.width() > parent.width() + 1:
            problems.append(f"{type(widget).__name__} '{_text(widget)}' sticks out of "
                            f"{type(parent).__name__} ({widget.x() + widget.width()} > {parent.width()})")
        if isinstance(widget, (QAbstractButton, QLineEdit)) or (isinstance(widget, QLabel) and not widget.wordWrap()):
            need = widget.minimumSizeHint().width()
            if widget.width() + 1 < need:
                problems.append(f"{type(widget).__name__} '{_text(widget)}' squeezed ({widget.width()} < {need})")
    return problems


def _text(widget: QWidget) -> str:
    return str(getattr(widget, "text", lambda: "")())[:30]


@pytest.mark.parametrize("size", [(1024, 700), (1280, 800)])
def test_no_view_is_clipped(win: Any, qtbot: Any, size: tuple[int, int]) -> None:  # noqa: F811
    from neuroplan.ui_qt.app import VIEW_NAMES
    win.resize(*size)
    total = sum(win.splitter.sizes())
    win.splitter.setSizes([total - config.COACH_MIN_WIDTH, config.COACH_MIN_WIDTH])
    qtbot.wait(30)
    assert win.minimumSizeHint().width() <= 1024, "the window's minimum width stays within 1024 px"
    assert win.width() == size[0]
    for name in VIEW_NAMES:
        win.show_view(name)
        qtbot.wait(30)
        assert clipped(win.views[name]) == [], name
    assert clipped(win.panel) == []


def test_no_clipping_in_every_theme_and_text_size(win: Any, qtbot: Any) -> None:  # noqa: F811
    from neuroplan.ui_qt.app import VIEW_NAMES
    win.resize(1024, 700)
    qtbot.wait(20)
    for theme in config.THEME_NAMES:
        win.set_theme(theme, store=False)
        for size in config.TEXT_SIZE_NAMES:
            win.set_text_size(size, store=False)
            qtbot.wait(20)
            if size != "Large":
                assert win.minimumSizeHint().width() <= 1024, (theme, size)
            for name in VIEW_NAMES:
                win.show_view(name)
                qtbot.wait(20)
                assert clipped(win.views[name]) == [], (theme, size, name, win.width())
    win.set_theme("Midnight", store=False)
    win.set_text_size("Medium", store=False)
