"""Theme and text size of the Qt UI: one stylesheet generated from the colour tokens, live switching.

Widgets carry roles as dynamic properties (``kind="primary"``, ``role="muted"``, ``card="true"``…) and the
stylesheet styles those roles, so no widget contains a colour. Painted items (chat bubbles) read the
same tokens through ``color()``. Font sizes are given at "Medium" and scaled by the text size."""
from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QWidget

from .. import config
from ..palette import THEMES

FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


class Look:
    """The theme and text size the UI currently shows (UI state of the running app)."""

    def __init__(self) -> None:
        self.theme = config.THEME_NAMES[0]
        self.text_size = "Medium"

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
        font = QFont(family)
        font.setPixelSize(self.px(base))
        if bold:
            font.setWeight(QFont.Weight.DemiBold)
        return font

    def chat_font(self, base: int = config.CHAT_FONT_SIZE, bold: bool = False) -> QFont:
        return self.font(base, bold)

    # ------------------------------------------------------------ switching
    def apply(self, app: Optional[QApplication] = None) -> None:
        """Install the stylesheet and default font for the current theme and text size."""
        app = app or QApplication.instance()  # type: ignore[assignment]
        if app is None:
            return
        app.setFont(self.font())
        app.setStyleSheet(self.stylesheet())

    def stylesheet(self) -> str:
        c = self.colors
        px = self.px
        return f"""
* {{ font-family: "{FAMILY}"; }}
QWidget {{ color: {c['text']}; font-size: {px(13)}px; }}
QMainWindow, QWidget#centre, QStackedWidget, QWidget[view="true"], QScrollArea[view="true"] > QWidget > QWidget
    {{ background: {c['bg']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWidget#sidebar {{ background: {c['sidebar']}; }}
QWidget#coachPanel, QWidget#coachStrip {{ background: {c['surface']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px 6px; }}

QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {px(22)}px; font-weight: 700; }}
QLabel[role="brand"] {{ font-size: {px(19)}px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: {px(15)}px; font-weight: 700; }}
QLabel[role="muted"] {{ color: {c['text_muted']}; }}
QLabel[role="small"] {{ color: {c['text_muted']}; font-size: {px(11)}px; }}
QLabel[role="caption"] {{ color: {c['text_muted']}; font-size: {px(10)}px; font-weight: 700; letter-spacing: 1px; }}
QLabel[role="strong"] {{ font-weight: 600; }}
QLabel[role="strong14"] {{ font-size: {px(14)}px; font-weight: 600; }}
QLabel[role="kpi"] {{ font-size: {px(24)}px; font-weight: 700; }}
QLabel[role="clock"] {{ font-family: "{MONO_FAMILY}"; font-size: {px(34)}px; font-weight: 700; }}
QLabel[role="time"] {{ font-family: "{MONO_FAMILY}"; font-size: {px(14)}px; font-weight: 700; }}
QLabel[tone="success"] {{ color: {c['success']}; }}
QLabel[tone="warning"] {{ color: {c['warning']}; }}
QLabel[tone="danger"] {{ color: {c['danger']}; }}
QLabel[tone="accent"] {{ color: {c['accent_text']}; }}
QLabel[tone="muted"] {{ color: {c['text_muted']}; }}
QLabel[toast="error"] {{ color: {c['danger']}; }}

QFrame[card="true"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame[card="alt"] {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame[card="today"] {{ background: {c['surface']}; border: 1px solid {c['accent']}; border-radius: 12px; }}
QFrame[card="selected"] {{ background: {c['surface']}; border: 1px solid {c['accent']}; border-radius: 12px; }}
QFrame[card="inset"] {{ background: {c['surface_alt']}; border: none; border-radius: 8px; }}
QFrame[card="banner"] {{ background: {c['banner_bg']}; border: none; border-radius: 10px; }}
QFrame[card="banner"] QLabel {{ color: {c['danger']}; font-weight: 600; }}
QFrame[card] QLabel {{ border: none; }}

QPushButton {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 5px 12px; font-size: {px(12)}px; }}
QPushButton:hover {{ background: {c['surface_hover']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus {{ border: 1px solid {c['accent']}; }}
QPushButton:disabled {{ color: {c['text_disabled']}; background: {c['surface_alt']}; }}
QPushButton[kind="primary"] {{ background: {c['accent']}; color: {c['on_accent']}; border: 1px solid {c['accent']};
    font-weight: 600; font-size: {px(13)}px; }}
QPushButton[kind="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[kind="primary"]:pressed {{ background: {c['accent_hover']}; border-color: {c['text']}; }}
QPushButton[kind="primary"]:focus {{ border: 1px solid {c['text']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['surface_hover']}; border-color: {c['border']};
    color: {c['text_disabled']}; }}
QPushButton[kind="nav"] {{ background: transparent; border: 1px solid transparent; text-align: left;
    padding: 8px 12px; font-weight: 600; font-size: {px(13)}px; }}
QPushButton[kind="nav"]:hover {{ background: {c['surface_alt']}; }}
QPushButton[kind="nav"]:checked {{ background: {c['surface_hover']}; }}
QPushButton[kind="nav"]:focus {{ border: 1px solid {c['accent']}; }}
QPushButton[kind="chip"] {{ border-radius: 10px; padding: 3px 10px; font-size: {px(11)}px; }}
QPushButton[kind="option"] {{ background: {c['surface']}; color: {c['accent_text']}; border: 1px solid {c['accent']};
    border-radius: 10px; padding: 3px 12px; font-weight: 600; font-size: {px(12)}px; }}
QPushButton[kind="option"]:hover {{ background: {c['surface_hover']}; }}
QPushButton[kind="option"]:disabled {{ color: {c['text_disabled']}; border-color: {c['border']}; }}
QPushButton[kind="link"] {{ background: transparent; border: 1px solid transparent; color: {c['accent_text']};
    padding: 1px 4px; font-weight: 600; }}
QPushButton[kind="link"]:hover {{ text-decoration: underline; }}
QPushButton[kind="link"]:focus {{ border: 1px solid {c['accent']}; }}
QPushButton[kind="icon"] {{ padding: 4px; min-width: 28px; }}
QPushButton[tone="danger"] {{ color: {c['danger']}; }}
QPushButton[tone="accent"] {{ color: {c['accent_text']}; }}
QPushButton[tone="success"] {{ color: {c['success']}; border-color: {c['success']}; }}
QPushButton[tone="warning"] {{ color: {c['warning']}; border-color: {c['warning']}; }}
QPushButton[tone="dangerline"] {{ color: {c['danger']}; border-color: {c['danger']}; }}
QPushButton[tone="accentline"] {{ color: {c['accent_text']}; border-color: {c['accent']}; }}
QLabel#badge {{ background: {c['danger']}; color: {c['on_danger']}; border-radius: 9px; padding: 0 5px;
    font-size: {px(11)}px; font-weight: 700; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ background: {c['surface_alt']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['text_disabled']}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']};
    selection-background-color: {c['surface_hover']}; selection-color: {c['text']}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['border']}; border-radius: 4px;
    background: {c['surface_alt']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox:focus {{ color: {c['accent_text']}; }}

QProgressBar {{ background: {c['surface_hover']}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ border-radius: 4px; background: {c['accent']}; }}
QProgressBar[tone="success"]::chunk {{ background: {c['success']}; }}
QProgressBar[tone="warning"]::chunk {{ background: {c['warning']}; }}
QProgressBar[tone="danger"]::chunk {{ background: {c['danger']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['scrollbar']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scrollbar_hover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['scrollbar']}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {c['border']}; }}
QSplitter::handle:pressed {{ background: {c['accent']}; }}
QListView#messages {{ background: {c['bg']}; border: none; border-radius: 10px; outline: none; }}
"""


LOOK = Look()


def set_prop(widget: QWidget, name: str, value: object) -> None:
    """Change a styling property at runtime and re-apply the stylesheet rules to the widget."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
