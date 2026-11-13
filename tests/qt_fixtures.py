"""Shared fixtures for the Qt UI tests (offscreen platform, see conftest.py)."""
from __future__ import annotations

from typing import Any, Iterator

import pytest

from neuroplan.db.repository import Repository

from .conftest import TODAY


def make_window(repo: Repository, qtbot: Any, show: bool = True) -> Any:
    from neuroplan.ui_qt.app import MainWindow
    window = MainWindow(repo, today_fn=lambda: TODAY, run_startup=False)
    qtbot.addWidget(window)
    if show:
        window.show()
        qtbot.waitExposed(window)
    return window


def close_window(window: Any) -> None:
    window._close_asked = True          # skip the end-of-day check-in on close
    window.close()


@pytest.fixture
def win(seeded: Repository, qtbot: Any) -> Iterator[Any]:
    window = make_window(seeded, qtbot)
    window.resize(1280, 800)
    qtbot.wait(20)
    yield window
    close_window(window)


def wait_for(qtbot: Any, condition: Any, timeout: int = 5000) -> None:
    qtbot.waitUntil(condition, timeout=timeout)
