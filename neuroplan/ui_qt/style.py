"""Theme and text size of the Qt UI, without stylesheets.

Qt stylesheets made every layout and paint pass about twice as slow (measured with tools/ui_check.py), so
the look is built from: the Fusion style with a QPalette made from the colour tokens, fonts and colours
set per role on labels, and a few custom-painted primitives (buttons, cards, splitter handle, badge — see
widgets.py) that read the tokens when they paint. Widgets carry their role as dynamic properties
(``role``, ``tone``, ``kind``, ``card``, ``bg``); ``restyle`` applies them, and a theme or text-size switch
re-applies them to every widget, live."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QLabel, QProgressBar, QSpinBox,
                               QToolTip, QWidget)

from .. import config
from ..palette import THEMES

FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

# role → (size at Medium, bold, family)
ROLE_FONTS: dict[str, tuple[int, bool, str]] = {
    "": (13, False, FAMILY), "title": (22, True, FAMILY), "brand": (19, True, FAMILY), "h2": (15, True, FAMILY),
    "muted": (13, False, FAMILY), "small": (11, False, FAMILY), "caption": (10, True, FAMILY),
    "kpi": (24, True, FAMILY), "clock": (34, True, MONO_FAMILY), "time": (14, True, MONO_FAMILY),
    "strong": (13, True, FAMILY), "strong14": (14, True, FAMILY)}
ROLE_COLOR = {"muted": "text_muted", "small": "text_muted", "caption": "text_muted"}
TONE_COLOR = {"success": "success", "warning": "warning", "danger": "danger", "accent": "accent_text",
              "muted": "text_muted"}


class Look:
    """The theme and text size the UI currently shows (UI state of the running app)."""

    def __init__(self) -> None:
        self.theme = config.THEME_NAMES[0]
        self.text_size = "Medium"
        self._fonts: dict[tuple[int, bool, str, str], QFont] = {}

    # ------------------------------------------------------------ values
    @property
    def colors(self) -> dict[str, str]:
        return THEMES[self.theme].colors

    def color(self, token: str) -> QColor:
        return QColor(self.colors[token])

    @property
    def factor(self) -> float:
        return config.CHAT_TEXT_SIZES[self.text_size] / config.CHAT_FONT_SIZE

    def px(self, base: int) -> int:
        """A font size given at "Medium", in the current text size (pixels)."""
        return max(8, round(base * self.factor))

    def font(self, base: int = config.UI_FONT_SIZE, bold: bool = False, family: str = FAMILY) -> QFont:
        key = (base, bold, family, self.text_size)
        cached = self._fonts.get(key)
        if cached is None:
            cached = QFont(family)
            cached.setPixelSize(self.px(base))
            cached.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
            cached.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            self._fonts[key] = cached
        return cached

    def chat_font(self, base: int = config.CHAT_FONT_SIZE, bold: bool = False) -> QFont:
        return self.font(base, bold)

    def palette(self) -> QPalette:
        c = self.colors
        pal = QPalette()
        roles = {QPalette.ColorRole.Window: "bg", QPalette.ColorRole.WindowText: "text",
                 QPalette.ColorRole.Base: "surface_alt", QPalette.ColorRole.AlternateBase: "surface",
                 QPalette.ColorRole.Text: "text", QPalette.ColorRole.Button: "surface_alt",
                 QPalette.ColorRole.ButtonText: "text", QPalette.ColorRole.BrightText: "on_danger",
                 QPalette.ColorRole.Highlight: "accent", QPalette.ColorRole.HighlightedText: "on_accent",
                 QPalette.ColorRole.ToolTipBase: "surface", QPalette.ColorRole.ToolTipText: "text",
                 QPalette.ColorRole.PlaceholderText: "text_muted", QPalette.ColorRole.Link: "accent_text",
                 QPalette.ColorRole.Light: "surface_hover", QPalette.ColorRole.Midlight: "surface_hover",
                 QPalette.ColorRole.Mid: "border", QPalette.ColorRole.Dark: "border",
                 QPalette.ColorRole.Shadow: "bg"}
        for role, token in roles.items():
            pal.setColor(role, QColor(c[token]))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["text_disabled"]))
        return pal

    # ------------------------------------------------------------ switching
    def apply(self, app: Optional[QApplication] = None) -> None:
        """Install the palette and default font for the current theme and text size, and re-style every
        existing widget (live switch)."""
        app = app or QApplication.instance()  # type: ignore[assignment]
        if app is None:
            return
        if app.style().name().lower() != "fusion":
            app.setStyle("Fusion")
        app.setStyleSheet("")
        palette = self.palette()
        app.setPalette(palette)
        QToolTip.setPalette(palette)
        app.setFont(self.font())
        for widget in app.allWidgets():
            restyle(widget)


LOOK = Look()


def restyle(widget: QWidget) -> None:
    """Apply the widget's role properties (font, colour, background) for the current look."""
    if isinstance(widget, QLabel):
        role = str(widget.property("role") or "")
        size, bold, family = ROLE_FONTS.get(role, ROLE_FONTS[""])
        widget.setFont(LOOK.font(size, bold, family))
        tone = str(widget.property("tone") or "")
        token = TONE_COLOR.get(tone) or ROLE_COLOR.get(role) or "text"
        _set_color(widget, QPalette.ColorRole.WindowText, token)
    elif isinstance(widget, QProgressBar):
        tone = str(widget.property("tone") or "")
        _set_color(widget, QPalette.ColorRole.Highlight, TONE_COLOR.get(tone, "accent"))
        _set_color(widget, QPalette.ColorRole.Base, "surface_hover")
        _set_color(widget, QPalette.ColorRole.Window, "surface_hover")
    elif isinstance(widget, QSpinBox):
        # wide enough for its largest value at the current font size (the spin buttons need ~36 px)
        text = widget.textFromValue(widget.maximum()) + widget.suffix()
        widget.setMinimumWidth(widget.fontMetrics().horizontalAdvance(text) + 40)
    restyle_hook = getattr(widget, "restyle", None)
    if callable(restyle_hook):
        restyle_hook()
    bg = widget.property("bg")
    if bg:
        target = widget.viewport() if isinstance(widget, QAbstractScrollArea) else widget
        pal = target.palette()
        pal.setColor(QPalette.ColorRole.Window, LOOK.color(str(bg)))
        pal.setColor(QPalette.ColorRole.Base, LOOK.color(str(bg)))
        target.setPalette(pal)
        target.setAutoFillBackground(True)
    widget.update()


def _set_color(widget: QWidget, role: QPalette.ColorRole, token: str) -> None:
    pal = widget.palette()
    color = LOOK.color(token)
    if pal.color(role) != color:
        pal.setColor(QPalette.ColorGroup.Active, role, color)
        pal.setColor(QPalette.ColorGroup.Inactive, role, color)
        widget.setPalette(pal)


def set_prop(widget: QWidget, name: str, value: Any) -> None:
    """Change a styling property at runtime and re-apply the widget's style."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    restyle(widget)


def set_bg(widget: QWidget, token: str) -> None:
    widget.setProperty("bg", token)
    restyle(widget)
