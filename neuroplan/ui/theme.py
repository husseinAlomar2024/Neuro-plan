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

# The tokens every theme defines. The first fourteen are the public design tokens; the rest are
# helpers for specific widgets (sidebar, hovered surfaces, accent-coloured text, switches, scrollbars).
TOKENS: tuple[str, ...] = (
    "bg", "surface", "surface_alt", "border", "text", "text_muted", "accent", "accent_hover", "on_accent",
    "user_bubble", "coach_bubble", "success", "warning", "danger",
    "sidebar", "surface_hover", "accent_text", "coach_bubble_border", "text_disabled", "on_danger",
    "banner_bg", "chip_text", "chip_text_muted", "scrollbar", "scrollbar_hover", "switch_track", "switch_knob",
)


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    colors: dict[str, str]


THEMES: dict[str, Theme] = {
    "Midnight": Theme("Midnight", True, {
        "bg": "#0F1218", "surface": "#171B24", "surface_alt": "#1F2430", "border": "#2A3040",
        "text": "#E6E9EF", "text_muted": "#8B93A7", "accent": "#7C9CF5", "accent_hover": "#93AEFA",
        "on_accent": "#0F1218", "user_bubble": "#2B3A66", "coach_bubble": "#1F2430", "success": "#5CC8A1",
        "warning": "#E5B454", "danger": "#EF6F6C",
        "sidebar": "#0B0E13", "surface_hover": "#283041", "accent_text": "#7C9CF5",
        "coach_bubble_border": "#1F2430", "text_disabled": "#999999", "on_danger": "#0F1218",
        "banner_bg": "#3A2224", "chip_text": "#10131A", "chip_text_muted": "#4A4F5C",
        "scrollbar": "#696969", "scrollbar_hover": "#878787", "switch_track": "#4A4D50",
        "switch_knob": "#D5D9DE"}),
    "Ivory & Gold": Theme("Ivory & Gold", False, {
        "bg": "#FBF8F1", "surface": "#FFFFFF", "surface_alt": "#F4EEDF", "border": "#E6DCC3",
        "text": "#2B2620", "text_muted": "#6F6657", "accent": "#C9A24A", "accent_hover": "#B8892B",
        "on_accent": "#2B2620", "user_bubble": "#F3E3B5", "coach_bubble": "#FFFFFF", "success": "#2E8B62",
        "warning": "#B06A10", "danger": "#C24B3F",
        "sidebar": "#F4EEDF", "surface_hover": "#EADFC4", "accent_text": "#7A5C14",
        "coach_bubble_border": "#E6DCC3", "text_disabled": "#B3A88F", "on_danger": "#FFFFFF",
        "banner_bg": "#F8E1DC", "chip_text": "#10131A", "chip_text_muted": "#4A4F5C",
        "scrollbar": "#D9CBA6", "scrollbar_hover": "#C2AE7E", "switch_track": "#D9CBA6",
        "switch_knob": "#FFFFFF"}),
    "Mist": Theme("Mist", False, {
        "bg": "#E9EEF3", "surface": "#F7F9FB", "surface_alt": "#DDE5EC", "border": "#C9D4DE",
        "text": "#1E2A36", "text_muted": "#526476", "accent": "#2F7D8C", "accent_hover": "#256673",
        "on_accent": "#FFFFFF", "user_bubble": "#CFE6EA", "coach_bubble": "#F7F9FB", "success": "#2E8B62",
        "warning": "#A0660A", "danger": "#B8433A",
        "sidebar": "#DDE5EC", "surface_hover": "#CFD9E3", "accent_text": "#256673",
        "coach_bubble_border": "#C9D4DE", "text_disabled": "#93A3B3", "on_danger": "#FFFFFF",
        "banner_bg": "#F5DEDB", "chip_text": "#10131A", "chip_text_muted": "#4A4F5C",
        "scrollbar": "#B8C6D3", "scrollbar_hover": "#9DAFC0", "switch_track": "#B8C6D3",
        "switch_knob": "#FFFFFF"}),
}


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


# ---------------------------------------------------------------- contrast (WCAG 2.x)
def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


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


def scrollable(master: object, **kwargs: object) -> ctk.CTkScrollableFrame:
    options: dict[str, object] = {"fg_color": "transparent", "scrollbar_button_color": SCROLLBAR,
                                  "scrollbar_button_hover_color": SCROLLBAR_HOVER}
    options.update(kwargs)
    return ctk.CTkScrollableFrame(master, **options)  # type: ignore[arg-type]


def autowrap(widget: ctk.CTkLabel, container: Any, inset: int, minimum: int = 120) -> None:
    """Keep ``widget``'s wraplength at the width of ``container`` minus ``inset`` (unscaled px), so the
    text re-wraps while a divider is dragged or the window is resized. One handler per container."""
    items: list[tuple[ctk.CTkLabel, int, int]] | None = getattr(container, "_np_autowrap", None)
    if items is None:
        items = []
        container._np_autowrap = items  # noqa: SLF001 - bookkeeping on the container itself
        container.bind("<Configure>", lambda event: _fit_wraps(container, event.width), add="+")
    items[:] = [item for item in items if item[0].winfo_exists()]
    items.append((widget, inset, minimum))
    if container.winfo_width() > 1:
        _fit_wraps(container, container.winfo_width())


def _fit_wraps(container: Any, real_width: int) -> None:
    items: list[tuple[ctk.CTkLabel, int, int]] = getattr(container, "_np_autowrap", [])
    items[:] = [item for item in items if item[0].winfo_exists()]
    for widget, inset, minimum in items:
        scale = ctk.ScalingTracker.get_widget_scaling(widget)
        width = max(minimum, int(real_width / scale) - inset)
        if widget.cget("wraplength") != width:
            widget.configure(wraplength=width)
