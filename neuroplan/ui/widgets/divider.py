"""A draggable 6 px strip between two side-by-side panes.

The divider owns no layout: it reports a new width for the pane it controls through ``on_drag`` while
the mouse moves (clamped to ``minimum`` and ``maximum()``), ``on_commit`` when the button is released,
and ``on_reset`` on a double click. Widths are unscaled px, like every CustomTkinter size."""
from __future__ import annotations

from typing import Any, Callable, Optional

import customtkinter as ctk

from ... import config
from .. import theme


class Divider(ctk.CTkFrame):
    def __init__(self, master: Any, get_width: Callable[[], int], on_drag: Callable[[int], None],
                 on_commit: Callable[[int], None], on_reset: Callable[[], None], minimum: int,
                 maximum: Callable[[], int], grows_left: bool = False) -> None:
        super().__init__(master, width=config.DIVIDER_WIDTH, corner_radius=0, fg_color="transparent")
        self.get_width = get_width
        self.on_drag = on_drag
        self.on_commit = on_commit
        self.on_reset = on_reset
        self.minimum = minimum
        self.maximum = maximum
        self.grows_left = grows_left          # True: the pane is right of the divider (dragging left grows it)
        self._start_x: Optional[int] = None
        self._start_width = 0
        self.dragging = False
        self.hovered = False
        self.configure(cursor="sb_h_double_arrow")
        for sequence, handler in (("<Enter>", self._enter), ("<Leave>", self._leave),
                                  ("<ButtonPress-1>", self._press), ("<B1-Motion>", self._motion),
                                  ("<ButtonRelease-1>", self._release), ("<Double-Button-1>", self._double)):
            self.bind(sequence, handler, add="+")

    # ------------------------------------------------------------ hover highlight
    def _enter(self, _event: Any = None) -> None:
        self.hovered = True
        self.configure(fg_color=theme.BORDER)

    def _leave(self, _event: Any = None) -> None:
        self.hovered = False
        if not self.dragging:
            self.configure(fg_color="transparent")

    # ------------------------------------------------------------ dragging (public for tests)
    def clamp(self, width: int) -> int:
        return max(self.minimum, min(max(self.minimum, self.maximum()), int(width)))

    def press(self, x_root: int) -> None:
        self._start_x = x_root
        self._start_width = self.get_width()
        self.dragging = True
        self.configure(fg_color=theme.ACCENT)

    def drag_to(self, x_root: int) -> int:
        """Move the divider; returns the new (clamped) width of the pane."""
        if self._start_x is None:
            return self.get_width()
        delta = (x_root - self._start_x) / ctk.ScalingTracker.get_widget_scaling(self)
        width = self.clamp(round(self._start_width - delta if self.grows_left else self._start_width + delta))
        self.on_drag(width)
        return width

    def release(self) -> None:
        if self._start_x is None:
            return
        self._start_x = None
        self.dragging = False
        self.configure(fg_color=theme.BORDER if self.hovered else "transparent")
        self.on_commit(self.get_width())

    def _press(self, event: Any) -> None:
        self.press(event.x_root)

    def _motion(self, event: Any) -> None:
        self.drag_to(event.x_root)

    def _release(self, _event: Any) -> None:
        self.release()

    def _double(self, _event: Any) -> None:
        self._start_x = None
        self.dragging = False
        self.on_reset()
