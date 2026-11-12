"""Run a callback once, a short moment after the last of a burst of calls (e.g. <Configure> events)."""
from __future__ import annotations

import tkinter
from typing import Any, Callable, Optional

from ... import config


class Debounce:
    """``Debounce(widget, fn)(...)`` schedules ``fn()`` ``ms`` after the latest call; earlier pending calls
    are dropped. ``fn`` reads the current sizes itself, so nothing stale from the events is used."""

    def __init__(self, widget: Any, fn: Callable[[], None], ms: int = config.RESIZE_DEBOUNCE_MS) -> None:
        self.widget = widget
        self.fn = fn
        self.ms = ms
        self._after: Optional[str] = None
        self.runs = 0                           # how often fn actually ran (for tests)

    @property
    def pending(self) -> bool:
        return self._after is not None

    def __call__(self, *_args: Any) -> None:
        self.cancel()
        try:
            self._after = self.widget.after(self.ms, self._run)
        except tkinter.TclError:                # the window is being destroyed
            self._after = None

    def cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tkinter.TclError:
                pass
            self._after = None

    def flush(self) -> None:
        """Run a pending call now."""
        if self._after is not None:
            self.cancel()
            self._run()

    def _run(self) -> None:
        self._after = None
        try:
            if not self.widget.winfo_exists():
                return
        except tkinter.TclError:
            return
        self.runs += 1
        self.fn()
