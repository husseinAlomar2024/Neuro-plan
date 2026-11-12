"""Chat bubble text with slightly looser line spacing than a Tk label allows.

The text is word-wrapped here (long words break too) and drawn line by line on a canvas exactly as
large as the text, so the bubble shrinks to short messages and grows with long ones. Like a label it
takes ``wraplength`` (unscaled px) through ``configure``/``cget``, and it follows the theme, the text
size and the DPI scaling live."""
from __future__ import annotations

import tkinter
import tkinter.font as tkfont
from typing import Any, Callable, Optional

import customtkinter as ctk

from ... import config
from .. import theme


def wrap_text(text: str, limit: int, measure: Callable[[str], int], space: int) -> list[str]:
    """Greedy word wrap of ``text`` to ``limit`` px. ``measure`` gives the width of a word, ``space`` the
    width of a blank. Explicit line breaks are kept; a word wider than the limit is broken."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line, width = "", 0
        for word in paragraph.split(" "):
            w = measure(word)
            if line and width + space + w <= limit:
                line, width = f"{line} {word}", width + space + w
                continue
            if line or width:
                lines.append(line)
            while w > limit and len(word) > 1:
                cut = _longest_prefix(word, limit, measure)
                lines.append(word[:cut])
                word = word[cut:]
                w = measure(word)
            line, width = word, w
        lines.append(line)
    return lines


def _longest_prefix(word: str, limit: int, measure: Callable[[str], int]) -> int:
    low, high = 1, len(word) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if measure(word[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    return max(1, low)


class ChatText(tkinter.Canvas):
    def __init__(self, master: Any, text: str, font: ctk.CTkFont, wraplength: int,
                 color: theme.Color = theme.TEXT, bg: theme.Color = theme.COACH_BUBBLE,
                 line_gap: int = config.CHAT_LINE_GAP) -> None:
        super().__init__(master, highlightthickness=0, borderwidth=0, bg=theme.resolve(bg))
        self.text = text
        self._font = font
        self._color = color
        self._bg = bg
        self._wrap = int(wraplength)
        self._gap = line_gap
        self._scale = ctk.ScalingTracker.get_widget_scaling(master)
        self._tkfont = tkfont.Font(root=self, family=font.cget("family"), size=-1, weight=font.cget("weight"))
        self._px = 0
        self._widths: dict[str, int] = {}
        self.lines: list[str] = []
        font.add_size_configure_callback(self._relayout)
        ctk.AppearanceModeTracker.add(self._appearance_changed, self)
        ctk.ScalingTracker.add_widget(self._scaling_changed, self)
        self._layout()

    # ------------------------------------------------------------ label-like options
    def configure(self, cnf: Optional[dict[str, Any]] = None, **kwargs: Any) -> Any:  # type: ignore[override]
        wrap = kwargs.pop("wraplength", None)
        result = super().configure(cnf, **kwargs) if (cnf or kwargs) else None
        if wrap is not None and int(wrap) != self._wrap:
            self._wrap = int(wrap)
            self._layout()
        return result

    config = configure  # type: ignore[assignment]

    def cget(self, key: str) -> Any:
        return self._wrap if key == "wraplength" else super().cget(key)

    # ------------------------------------------------------------ live updates
    def _relayout(self) -> None:
        if self.winfo_exists():
            self._layout()

    def _appearance_changed(self, _mode: str) -> None:
        if self.winfo_exists():
            tkinter.Canvas.configure(self, bg=theme.resolve(self._bg))
            self.itemconfigure("line", fill=theme.resolve(self._color))

    def _scaling_changed(self, widget_scaling: float, _window_scaling: float) -> None:
        self._scale = widget_scaling
        self._relayout()

    def destroy(self) -> None:
        ctk.AppearanceModeTracker.remove(self._appearance_changed)
        ctk.ScalingTracker.remove_widget(self._scaling_changed, self)
        try:
            self._font.remove_size_configure_callback(self._relayout)
        except ValueError:
            pass
        super().destroy()

    # ------------------------------------------------------------ layout
    def _measure(self, word: str) -> int:
        width = self._widths.get(word)
        if width is None:
            width = self._tkfont.measure(word)
            self._widths[word] = width
        return width

    def _layout(self) -> None:
        px = max(1, round(int(self._font.cget("size")) * self._scale))
        if px != self._px:
            self._px = px
            self._widths.clear()
            self._tkfont.configure(size=-px, weight=self._font.cget("weight"))
        limit = max(1, round(self._wrap * self._scale))
        self.lines = wrap_text(self.text, limit, self._measure, self._measure(" "))
        line_height = int(self._tkfont.metrics("linespace"))
        gap = round(self._gap * self._scale)
        width = max((self._tkfont.measure(line) for line in self.lines), default=0) + 1
        height = len(self.lines) * line_height + max(0, len(self.lines) - 1) * gap
        self.delete("all")
        fill = theme.resolve(self._color)
        for i, line in enumerate(self.lines):
            self.create_text(0, i * (line_height + gap), text=line, anchor="nw", font=self._tkfont, fill=fill,
                             tags=("line",))
        tkinter.Canvas.configure(self, width=width, height=height)
