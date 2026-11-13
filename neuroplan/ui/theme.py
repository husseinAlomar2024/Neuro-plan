"""Every colour of the UI, as named tokens of three themes, plus small style helpers.

A colour is a ``Token``: a CustomTkinter colour "pair" whose both halves always resolve to the current
theme's value. CustomTkinter widgets keep the token object they were given and re-resolve it on every
redraw, so switching the theme only has to change the palette and ask every widget to redraw
(``set_theme``). No other module contains a colour literal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

import customtkinter as ctk

from .. import config
from . import fonts

from ..palette import THEMES, TOKENS, Theme, contrast_ratio, luminance  # noqa: F401

class _ThemeState:
    """The theme the UI currently shows (UI state, like CustomTkinter's appearance-mode tracker)."""

    def __init__(self) -> None:
        self.name = config.THEME_NAMES[0]

    @property
    def colors(self) -> dict[str, str]:
        return THEMES[self.name].colors


STATE = _ThemeState()


class Token(tuple):  # type: ignore[type-arg]
    """A named colour. To CustomTkinter it is a (light, dark) pair; both halves are the current
    theme's value, so a widget that redraws always shows the current theme."""

    def __new__(cls, name: str) -> "Token":
        if name not in TOKENS:
            raise KeyError(f"unknown colour token {name!r}")
        return super().__new__(cls, (name, name))

    @property
    def name(self) -> str:
        return str(tuple.__getitem__(self, 0))

    @property
    def hex(self) -> str:
        return STATE.colors[self.name]

    def __getitem__(self, index: Any) -> str:  # type: ignore[override]
        return self.hex

    def __iter__(self) -> Any:
        return iter((self.hex, self.hex))

    def __repr__(self) -> str:
        return f"Token({self.name!r})"


Color = Union[Token, str]

BG = Token("bg")
SIDEBAR = Token("sidebar")
SURFACE = Token("surface")
SURFACE_2 = Token("surface_alt")
SURFACE_3 = Token("surface_hover")
BORDER = Token("border")
TEXT = Token("text")
MUTED = Token("text_muted")
DISABLED = Token("text_disabled")
ACCENT = Token("accent")
ACCENT_HOVER = Token("accent_hover")
ACCENT_TEXT = Token("accent_text")
ON_ACCENT = Token("on_accent")
ON_DANGER = Token("on_danger")
SUCCESS = Token("success")
WARNING = Token("warning")
DANGER = Token("danger")
USER_BUBBLE = Token("user_bubble")
COACH_BUBBLE = Token("coach_bubble")
COACH_BUBBLE_BORDER = Token("coach_bubble_border")
BANNER_BG = Token("banner_bg")
CHIP_TEXT = Token("chip_text")
CHIP_TEXT_MUTED = Token("chip_text_muted")
SCROLLBAR = Token("scrollbar")
SCROLLBAR_HOVER = Token("scrollbar_hover")
SWITCH_TRACK = Token("switch_track")
SWITCH_KNOB = Token("switch_knob")

SIDEBAR_WIDTH = 168
PAD = 14

TYPE_LABEL = {"CATCHUP": "Catch-up", "PROGRESS": "Progress", "REVIEW": "Review", "PRACTICE": "Practice"}
STATUS_COLOR: dict[str, Token] = {"done": SUCCESS, "partial": WARNING, "skipped": MUTED, "pending": TEXT}

# CustomTkinter's own defaults, for every colour a widget gets without being told: tokens as well.
_CTK_DEFAULTS: dict[str, dict[str, Token]] = {
    "CTk": {"fg_color": BG},
    "CTkToplevel": {"fg_color": BG},
    "CTkFrame": {"fg_color": SURFACE, "top_fg_color": SURFACE_2, "border_color": BORDER},
    "CTkButton": {"fg_color": ACCENT, "hover_color": ACCENT_HOVER, "border_color": BORDER, "text_color": ON_ACCENT,
                  "text_color_disabled": DISABLED},
    "CTkLabel": {"text_color": TEXT, "border_color": BORDER},
    "CTkEntry": {"fg_color": SURFACE_2, "border_color": BORDER, "text_color": TEXT,
                 "placeholder_text_color": MUTED},
    "CTkSwitch": {"fg_color": SWITCH_TRACK, "progress_color": ACCENT, "button_color": SWITCH_KNOB,
                  "button_hover_color": SWITCH_KNOB, "text_color": TEXT, "text_color_disabled": DISABLED},
    "CTkProgressBar": {"fg_color": SURFACE_3, "progress_color": ACCENT, "border_color": BORDER},
    "CTkScrollbar": {"button_color": SCROLLBAR, "button_hover_color": SCROLLBAR_HOVER},
    "CTkOptionMenu": {"fg_color": SURFACE_2, "button_color": SURFACE_3, "button_hover_color": BORDER,
                      "text_color": TEXT, "text_color_disabled": DISABLED},
    "DropdownMenu": {"fg_color": SURFACE, "hover_color": SURFACE_3, "text_color": TEXT},
    "CTkTextbox": {"fg_color": SURFACE_2, "border_color": BORDER, "text_color": TEXT,
                   "scrollbar_button_color": SCROLLBAR, "scrollbar_button_hover_color": SCROLLBAR_HOVER},
    "CTkScrollableFrame": {"label_fg_color": SURFACE_2},
}


def install_defaults() -> None:
    """Point CustomTkinter's default colours at the tokens (before any widget is built)."""
    defaults = ctk.ThemeManager.theme
    for widget, colors in _CTK_DEFAULTS.items():
        if widget in defaults:
            for key, token in colors.items():
                if key in defaults[widget]:
                    defaults[widget][key] = token


def current_theme() -> str:
    return STATE.name


def apply_appearance(name: str) -> None:
    """Set the theme before the window is built (no redraw)."""
    install_defaults()
    STATE.name = config.normalize_theme(name)
    ctk.set_appearance_mode("Dark" if THEMES[STATE.name].dark else "Light")


def set_theme(name: str) -> bool:
    """Switch the theme live: every CustomTkinter widget (and every widget that registered with the
    appearance-mode tracker) redraws with the new palette. True when the theme changed."""
    name = config.normalize_theme(name)
    if name == STATE.name:
        return False
    STATE.name = name
    mode = "Dark" if THEMES[name].dark else "Light"
    if ctk.get_appearance_mode() != mode:
        ctk.set_appearance_mode(mode)                # redraws every registered widget
    else:
        ctk.AppearanceModeTracker.update_callbacks()
    return True


def next_theme(name: str) -> str:
    names = list(config.THEME_NAMES)
    return names[(names.index(config.normalize_theme(name)) + 1) % len(names)]


def resolve(color: Color) -> str:
    """The hex value of a token in the current theme (other colours are returned unchanged)."""
    return color.hex if isinstance(color, Token) else color


# ---------------------------------------------------------------- fonts and helpers
def font(size: int = config.UI_FONT_SIZE, weight: str = "normal", slant: str = "roman") -> ctk.CTkFont:
    return fonts.font(size, weight, slant)


def mono(size: int = config.UI_FONT_SIZE, weight: str = "normal") -> ctk.CTkFont:
    return fonts.mono(size, weight)


def backlog_color(units: int) -> Token:
    """Green 0, amber 1-2, red >= 3 (SPEC section 7.3)."""
    if units <= 0:
        return SUCCESS
    return WARNING if units <= 2 else DANGER


def primary_button(master: object, text: str, command: object = None, width: int = 110,
                   height: int = 32) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=height, corner_radius=8,
                         fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT,
                         text_color_disabled=DISABLED, font=font(13, "bold"))


def ghost_button(master: object, text: str, command: object = None, width: int = 90, height: int = 30,
                 text_color: Color = TEXT) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=height, corner_radius=8,
                         fg_color=SURFACE_2, hover_color=SURFACE_3, text_color=text_color,
                         text_color_disabled=DISABLED, border_width=1, border_color=BORDER, font=font(12))


def card(master: object, **kwargs: object) -> ctk.CTkFrame:
    options: dict[str, object] = {"fg_color": SURFACE, "corner_radius": 12, "border_width": 1,
                                  "border_color": BORDER}
    options.update(kwargs)
    return ctk.CTkFrame(master, **options)  # type: ignore[arg-type]


def label(master: object, text: str = "", size: int = config.UI_FONT_SIZE, weight: str = "normal",
          color: Color = TEXT, **kwargs: object) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=font(size, weight), text_color=color, **kwargs)  # type: ignore[arg-type]


def no_nested_idle_flush(widget: Any) -> Any:
    """CTkScrollbar and CTkOptionMenu end every redraw with ``self._canvas.update_idletasks()``: a full,
    nested geometry-and-paint pass of the whole window. A scrollbar is redrawn on every size change of
    its list, so during a relayout these nested passes paint half-finished layouts (broken frames) and
    cost most of the time. Their canvases redraw at the next idle point anyway, so the call is dropped."""
    canvas = getattr(widget, "_canvas", None)
    if canvas is not None:
        canvas.update_idletasks = _no_op
    return widget


def _no_op() -> None:
    return None


def scrollable(master: object, **kwargs: object) -> ctk.CTkScrollableFrame:
    options: dict[str, object] = {"fg_color": "transparent", "scrollbar_button_color": SCROLLBAR,
                                  "scrollbar_button_hover_color": SCROLLBAR_HOVER}
    options.update(kwargs)
    frame = ctk.CTkScrollableFrame(master, **options)  # type: ignore[arg-type]
    no_nested_idle_flush(frame._scrollbar)  # noqa: SLF001
    return frame


def autowrap(widget: ctk.CTkLabel, container: Any, inset: int, minimum: int = 120) -> None:
    """Keep ``widget``'s wraplength at the width of ``container`` minus ``inset`` (unscaled px), so the
    text re-wraps while a divider is dragged or the window is resized. One handler per container."""
    items: list[tuple[ctk.CTkLabel, int, int]] | None = getattr(container, "_np_autowrap", None)
    if items is None:
        items = []
        container._np_autowrap = items  # noqa: SLF001 - bookkeeping on the container itself
        from .widgets.debounce import Debounce      # (late import: widgets import this module)
        later = Debounce(container, lambda: _fit_wraps(container, container.winfo_width()))
        container._np_autowrap_later = later  # noqa: SLF001
        container.bind("<Configure>", lambda _event: later(), add="+")   # bound once per container
    items[:] = [item for item in items if item[0].winfo_exists()]
    items.append((widget, inset, minimum))
    if container.winfo_width() > 1:                 # fit just the new label now; the others already fit
        _fit_one(widget, inset, minimum, container.winfo_width())


def _fit_wraps(container: Any, real_width: int) -> None:
    if real_width <= 1:
        return
    items: list[tuple[ctk.CTkLabel, int, int]] = getattr(container, "_np_autowrap", [])
    items[:] = [item for item in items if item[0].winfo_exists()]
    for widget, inset, minimum in items:
        _fit_one(widget, inset, minimum, real_width)


def _fit_one(widget: ctk.CTkLabel, inset: int, minimum: int, real_width: int) -> None:
    scale = ctk.ScalingTracker.get_widget_scaling(widget)
    width = max(minimum, int(real_width / scale) - inset)
    if widget.cget("wraplength") != width:           # unchanged width: no reconfigure, no redraw
        widget.configure(wraplength=width)
