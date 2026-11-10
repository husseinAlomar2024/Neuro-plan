"""Colours, fonts and small style helpers. Every colour is a (light, dark) pair for CustomTkinter."""
from __future__ import annotations

import customtkinter as ctk

Color = tuple[str, str]

BG: Color = ("#F3F4F8", "#0F1218")
SIDEBAR: Color = ("#E8EAF1", "#0B0E13")
SURFACE: Color = ("#FFFFFF", "#171B24")
SURFACE_2: Color = ("#EEF0F6", "#1F2430")
SURFACE_3: Color = ("#E1E5EE", "#283041")
BORDER: Color = ("#D5D9E4", "#2A3040")
TEXT: Color = ("#1B2030", "#E6E9EF")
MUTED: Color = ("#667089", "#8B93A7")
ACCENT: Color = ("#4F6FE0", "#7C9CF5")
ACCENT_HOVER: Color = ("#405CC4", "#93AEFA")
ON_ACCENT: Color = ("#FFFFFF", "#0F1218")
SUCCESS: Color = ("#2E9B74", "#5CC8A1")
WARNING: Color = ("#B9801A", "#E5B454")
DANGER: Color = ("#CC4B48", "#EF6F6C")
USER_BUBBLE: Color = ("#DDE5FF", "#2B3A66")
COACH_BUBBLE: Color = ("#EEF0F6", "#1F2430")

FONT_FAMILY = "Segoe UI"
COACH_WIDTH = 380
SIDEBAR_WIDTH = 168
PAD = 14

TYPE_LABEL = {"CATCHUP": "Catch-up", "PROGRESS": "Progress", "REVIEW": "Review", "PRACTICE": "Practice"}
STATUS_COLOR: dict[str, Color] = {"done": SUCCESS, "partial": WARNING, "skipped": MUTED, "pending": TEXT}


def font(size: int = 13, weight: str = "normal", slant: str = "roman") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight, slant=slant)  # type: ignore[arg-type]


def mono(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Consolas", size=size, weight=weight)  # type: ignore[arg-type]


def apply_appearance(mode: str) -> None:
    ctk.set_appearance_mode("Light" if str(mode).lower() == "light" else "Dark")


def backlog_color(units: int) -> Color:
    """Green 0, amber 1-2, red >= 3 (SPEC section 7.3)."""
    if units <= 0:
        return SUCCESS
    return WARNING if units <= 2 else DANGER


def primary_button(master: object, text: str, command: object = None, width: int = 110,
                   height: int = 32) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=height, corner_radius=8,
                         fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, font=font(13, "bold"))


def ghost_button(master: object, text: str, command: object = None, width: int = 90, height: int = 30,
                 text_color: Color = TEXT) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=height, corner_radius=8,
                         fg_color=SURFACE_2, hover_color=SURFACE_3, text_color=text_color,
                         border_width=1, border_color=BORDER, font=font(12))


def card(master: object, **kwargs: object) -> ctk.CTkFrame:
    options: dict[str, object] = {"fg_color": SURFACE, "corner_radius": 12, "border_width": 1,
                                  "border_color": BORDER}
    options.update(kwargs)
    return ctk.CTkFrame(master, **options)  # type: ignore[arg-type]


def label(master: object, text: str = "", size: int = 13, weight: str = "normal", color: Color = TEXT,
          **kwargs: object) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=font(size, weight), text_color=color, **kwargs)  # type: ignore[arg-type]
