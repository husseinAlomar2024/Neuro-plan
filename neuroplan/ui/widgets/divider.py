"""A draggable 6 px strip between two side-by-side panes.

Ghost drag: while the mouse moves only a thin accent line (placed over the window) follows it, clamped
to ``minimum`` / ``maximum()`` so the limit is visible. No pane is resized during the drag; on release
the new width is applied once through ``on_apply`` and stored through ``on_commit``. A double click calls
``on_reset``. Widths are unscaled px, like every CustomTkinter size."""
from __future__ import annotations

import tkinter
from typing import Any, Callable, Optional

import customtkinter as ctk

from ... import config
from .. import theme

GHOST_WIDTH = 2          # px of the line shown while dragging


class Divider(ctk.CTkFrame):
    def __init__(self, master: Any, get_width: Callable[[], int], on_apply: Callable[[int], None],
                 on_commit: Callable[[int], None], on_reset: Callable[[], None], minimum: int,
                 maximum: Callable[[], int], grows_left: bool = False) -> None:
        super().__init__(master, width=config.DIVIDER_WIDTH, corner_radius=0, fg_color="transparent")
        self.get_width = get_width
        self.on_apply = on_apply
        self.on_commit = on_commit
        self.on_reset = on_reset
        self.minimum = minimum
        self.maximum = maximum
        self.grows_left = grows_left          # True: the pane is right of the divider (dragging left grows it)
        self._start_x: Optional[int] = None
        self._start_width = 0
        self._origin = 0                      # x of the divider's centre in the window, at press time
        self.pending_width: Optional[int] = None
        self.dragging = False
        self.hovered = False
        self.ghost: Optional[tkinter.Frame] = None
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

    def _scale(self) -> float:
        return float(ctk.ScalingTracker.get_widget_scaling(self))

    def press(self, x_root: int) -> None:
        self._start_x = x_root
        self._start_width = self.get_width()
        self.pending_width = self._start_width
        self.dragging = True
        top = self.winfo_toplevel()
        self._origin = self.winfo_rootx() - top.winfo_rootx() + self.winfo_width() // 2
        if self.ghost is None or not self.ghost.winfo_exists():
            self.ghost = tkinter.Frame(top, width=GHOST_WIDTH, borderwidth=0, highlightthickness=0,
                                       cursor="sb_h_double_arrow")
        self.ghost.configure(bg=theme.resolve(theme.ACCENT))
        self._place_ghost(self._start_width)
        self.ghost.lift()

    def _place_ghost(self, width: int) -> None:
        if self.ghost is None:
            return
        moved = (width - self._start_width) * self._scale()
        x = self._origin - moved if self.grows_left else self._origin + moved
        self.ghost.place(x=round(x) - GHOST_WIDTH // 2, y=0, width=GHOST_WIDTH, relheight=1.0)

    def ghost_x(self) -> Optional[int]:
        """Where the line is (window px), None when no drag is shown."""
        if self.ghost is None:
            return None
        info = self.ghost.place_info()
        return int(info["x"]) if info else None

    def drag_to(self, x_root: int) -> int:
        """Move the line; returns the width the pane will get on release. Nothing is resized here."""
        if self._start_x is None:
            return self.get_width()
        delta = (x_root - self._start_x) / self._scale()
        width = self.clamp(round(self._start_width - delta if self.grows_left else self._start_width + delta))
        if width != self.pending_width:
            self.pending_width = width
            self._place_ghost(width)
        return width

    def release(self) -> None:
        if self._start_x is None:
            return
        width = self.pending_width if self.pending_width is not None else self.get_width()
        self._end_drag()
        if width != self.get_width():
            self.on_apply(width)                 # the one and only resize of this drag
        self.on_commit(self.get_width())

    def _end_drag(self) -> None:
        self._start_x = None
        self.pending_width = None
        self.dragging = False
        if self.ghost is not None:
            self.ghost.place_forget()
        self.configure(fg_color=theme.BORDER if self.hovered else "transparent")

    def _press(self, event: Any) -> None:
        self.press(event.x_root)

    def _motion(self, event: Any) -> None:
        self.drag_to(event.x_root)

    def _release(self, _event: Any) -> None:
        self.release()

    def _double(self, _event: Any) -> None:
        self._end_drag()
        self.on_reset()
