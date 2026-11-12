"""Run a callback once, a short moment after the last of a burst of calls (e.g. <Configure> events)."""
from __future__ import annotations

import tkinter
import weakref
from typing import Any, Callable, Optional

from ... import config


class Debounce:
    """``Debounce(widget, fn)(...)`` schedules ``fn()`` ``ms`` after the latest call; earlier pending calls
    are dropped. ``fn`` reads the current sizes itself, so nothing stale from the events is used.
    ``flush_all`` runs every pending call at once (an atomic layout pass must not leave work for later)."""

    _pending: "weakref.WeakSet[Debounce]" = weakref.WeakSet()
    held = False        # True during an atomic layout pass: timers wait, the pass flushes explicitly

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
            Debounce._pending.add(self)
        except tkinter.TclError:                # the window is being destroyed
            self._after = None

    def cancel(self) -> None:
        if self._after is not None and self._after != "held":
            try:
                self.widget.after_cancel(self._after)
            except tkinter.TclError:
                pass
            self._after = None
        Debounce._pending.discard(self)

    @classmethod
    def flush_all(cls) -> int:
        """Run every pending debounced call now; returns how many ran."""
        ran = 0
        for item in list(cls._pending):
            if item.pending:
                item.flush()
                ran += 1
        return ran

    @classmethod
    def release_held(cls) -> None:
        """After an atomic pass: timers that came due during it are scheduled again (normal delay)."""
        for item in list(cls._pending):
            if item._after == "held":
                item._after = None
                item()

    def flush(self) -> None:
        """Run a pending call now."""
        if self._after is not None:
            self.cancel()
            self._execute()

    def _run(self) -> None:
        self._after = None
        if Debounce.held:                       # inside an atomic layout pass: flush_all runs it
            self._after = "held"
            return
        self._execute()

    def _execute(self) -> None:
        self._after = None
        Debounce._pending.discard(self)
        try:
            if not self.widget.winfo_exists():
                return
        except tkinter.TclError:
            return
        self.runs += 1
        self.fn()
