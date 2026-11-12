"""A draggable 6 px strip between two side-by-side panes.

Ghost drag: while the mouse moves only a 2 px accent line follows it, clamped to ``minimum`` /
``maximum()`` so the limit is visible. On Windows the line is its own small top-level window (composited
by the window manager), so moving it never leaves copies on the window below; elsewhere it is a frame
placed over the window. No pane is resized during the drag: on release the new width is handed to
``on_apply`` once (the app applies it atomically) and stored through ``on_commit``. Escape, losing the
focus or ``cancel()`` end the drag without a resize; a double click calls ``on_reset``.
Widths are unscaled px, like every CustomTkinter size."""
from __future__ import annotations

import tkinter
from typing import Any, Callable, Optional

import customtkinter as ctk

from ... import config
from .. import theme, win32

GHOST_WIDTH = 2          # px of the line shown while dragging


class Divider(ctk.CTkFrame):
    def __init__(self, master: Any, get_width: Callable[[], int], on_apply: Callable[[int], None],
                 on_commit: Callable[[int], None], on_reset: Callable[[], None], minimum: Callable[[], int],
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
        self._origin = 0                      # screen x of the divider's centre at press time
        self.pending_width: Optional[int] = None
        self.dragging = False
        self.hovered = False
        self._line: Optional[win32.LineWindow] = None       # Windows: the ghost line is a popup window
        self._frame_line: Optional[tkinter.Frame] = None    # elsewhere: a frame placed over the window
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
        low = self.minimum()
        return max(low, min(max(low, self.maximum()), int(width)))

    def _scale(self) -> float:
        return float(ctk.ScalingTracker.get_widget_scaling(self))

    def press(self, x_root: int) -> None:
        self._start_x = x_root
        self._start_width = self.get_width()
        self.pending_width = self._start_width
        self.dragging = True
        self._origin = self.winfo_rootx() + self.winfo_width() // 2
        self._show_line(self._start_width)

    def _line_x(self, width: int) -> int:
        """Screen x of the line's left edge for a pane ``width`` px wide."""
        moved = (width - self._start_width) * self._scale()
        return round(self._origin - moved if self.grows_left else self._origin + moved) - GHOST_WIDTH // 2

    def _show_line(self, width: int) -> None:
        top = self.winfo_toplevel()
        x = self._line_x(width)
        hwnd = win32.toplevel_hwnd(top)
        if hwnd:
            if self._line is None:
                self._line = win32.LineWindow(hwnd)
            self._line.show(x, top.winfo_rooty(), GHOST_WIDTH, max(1, top.winfo_height()),
                            theme.resolve(theme.ACCENT))
            return
        if self._frame_line is None or not self._frame_line.winfo_exists():
            self._frame_line = tkinter.Frame(top, width=GHOST_WIDTH, borderwidth=0, highlightthickness=0)
        self._frame_line.configure(bg=theme.resolve(theme.ACCENT))
        self._frame_line.place(x=x - top.winfo_rootx(), y=0, width=GHOST_WIDTH, relheight=1.0)
        self._frame_line.lift()

    def _move_line(self, width: int) -> None:
        x = self._line_x(width)
        if self._line is not None and self._line.visible:
            self._line.move(x, self._line.rect[1])
        elif self._frame_line is not None:
            self._frame_line.place_configure(x=x - self.winfo_toplevel().winfo_rootx())

    def hide_line(self) -> None:
        if self._line is not None:
            self._line.hide()
        if self._frame_line is not None:
            self._frame_line.place_forget()

    def ghost_line(self) -> Optional[tuple[tuple[int, int, int, int], str]]:
        """(screen rect, colour) of the ghost-line popup while it is shown (Windows)."""
        if self._line is not None and self._line.visible:
            return self._line.rect, self._line.color
        return None

    def ghost_x(self) -> Optional[int]:
        """The line's left edge in window px, None when no line is shown."""
        top = self.winfo_toplevel()
        if self._line is not None and self._line.visible:
            return self._line.rect[0] - top.winfo_rootx()
        if self._frame_line is not None and self._frame_line.place_info():
            return int(self._frame_line.place_info()["x"])
        return None

    def drag_to(self, x_root: int) -> int:
        """Move the line; returns the width the pane will get on release. Nothing is resized here."""
        if self._start_x is None:
            return self.get_width()
        delta = (x_root - self._start_x) / self._scale()
        width = self.clamp(round(self._start_width - delta if self.grows_left else self._start_width + delta))
        if width != self.pending_width:
            self.pending_width = width
            self._move_line(width)
        return width

    def release(self) -> None:
        if self._start_x is None:
            return
        width = self.pending_width if self.pending_width is not None else self.get_width()
        changed = width != self.get_width()
        if changed:
            self.on_apply(width)             # the one and only resize of this drag (hides the line too)
        self._end_drag()
        if changed:
            self.on_commit(self.get_width())

    def cancel(self) -> None:
        """End the drag without resizing anything (Escape, focus lost)."""
        if self.dragging:
            self._end_drag()

    def _end_drag(self) -> None:
        self._start_x = None
        self.pending_width = None
        self.dragging = False
        self.hide_line()
        self.hovered = self._pointer_over()       # the pane moved: the divider may now be under the mouse
        self.configure(fg_color=theme.BORDER if self.hovered else "transparent")

    def _pointer_over(self) -> bool:
        try:
            under = self.winfo_containing(*self.winfo_pointerxy())
        except (KeyError, tkinter.TclError):
            return False
        return under is not None and (under is self or str(under).startswith(str(self) + "."))

    def destroy(self) -> None:
        if self._line is not None:
            self._line.destroy()
        super().destroy()

    def _press(self, event: Any) -> None:
        self.press(event.x_root)

    def _motion(self, event: Any) -> None:
        self.drag_to(event.x_root)

    def _release(self, _event: Any) -> None:
        self.release()

    def _double(self, _event: Any) -> None:
        self._end_drag()
        self.on_reset()
