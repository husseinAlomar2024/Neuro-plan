"""The colour tokens of the three themes and WCAG contrast helpers. No UI toolkit here: the Qt UI
generates its stylesheet from these, tests check their contrast."""
from __future__ import annotations

from dataclasses import dataclass

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


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
