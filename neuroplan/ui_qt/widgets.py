"""Small shared Qt widgets and constructors. Styling comes from roles (see style.py), never colour literals.
Buttons, cards, the splitter handle and the badge paint themselves from the theme tokens (fast, no
stylesheets)."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QSplitter, QSplitterHandle, QVBoxLayout, QWidget)

from .style import LOOK, restyle


def _subject_color(color: str) -> QColor:
    """A subject's colour, or the muted text colour when it is not a valid colour."""
    value = QColor.fromString(color) if color else QColor()
    return value if value.isValid() else QColor(LOOK.colors["text_muted"])


def label(text: str = "", role: Optional[str] = None, tone: Optional[str] = None, wrap: bool = False,
          selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    if tone:
        widget.setProperty("tone", tone)
    if wrap:
        widget.setWordWrap(True)
        widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    restyle(widget)
    return widget


class ElidedLabel(QLabel):
    """A one-line label that shrinks by eliding ("…") instead of wrapping: cheap to lay out (no
    height-for-width), and the full text is in the tooltip when it is cut."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self.fontMetrics().horizontalAdvance("…") * 4 + 8), hint.height())

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        metrics = self.fontMetrics()
        text = metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        self.setToolTip(self.text() if text != self.text() else "")
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(self.rect(), int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), text)


def elided(text: str = "", role: Optional[str] = None, tone: Optional[str] = None) -> ElidedLabel:
    widget = ElidedLabel(text)
    if role:
        widget.setProperty("role", role)
    if tone:
        widget.setProperty("tone", tone)
    widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)  # full width when there is room
    restyle(widget)
    return widget


class CheckBox(QCheckBox):
    """A checkbox whose box and tick are painted from the theme (visible in the light themes too)."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 30, max(22, metrics.height() + 6))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def hitButton(self, pos: object) -> bool:  # noqa: N802 - Qt API
        return self.rect().contains(pos)  # type: ignore[arg-type]

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = 16
        box = QRectF(1.5, (self.height() - size) / 2 + 0.5, size - 1, size - 1)
        checked = self.isChecked()
        edge = "accent" if (checked or self.hasFocus() or self.underMouse()) else "text_muted"
        painter.setPen(QPen(LOOK.color(edge), 1.2))
        painter.setBrush(LOOK.color("accent" if checked else "surface"))
        painter.drawRoundedRect(box, 4, 4)
        if checked:
            pen = QPen(LOOK.color("on_accent"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            x, y = box.x(), box.y()
            painter.drawPolyline([QPointF(x + 3.5, y + 7.5), QPointF(x + 6.5, y + 10.5), QPointF(x + 11.5, y + 4.5)])
        painter.setPen(LOOK.color("text" if self.isEnabled() else "text_disabled"))
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(size + 9, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter), self.text())


# ---------------------------------------------------------------- buttons
_BUTTON_FONT = {"primary": (13, True), "nav": (13, True), "chip": (11, False), "option": (12, True),
                "link": (12, True), "icon": (13, False), "": (12, False)}
_BUTTON_PAD = {"primary": (14, 6), "nav": (12, 8), "chip": (10, 3), "option": (12, 4), "link": (5, 2),
               "icon": (6, 4), "": (12, 5)}
_TONE_TEXT = {"danger": "danger", "accent": "accent_text", "success": "success", "warning": "warning",
              "dangerline": "danger", "accentline": "accent_text"}
_TONE_BORDER = {"success": "success", "warning": "warning", "dangerline": "danger", "accentline": "accent"}


class Button(QPushButton):
    """A flat button painted from the theme: hover, pressed, checked, focus and disabled states."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None, kind: str = "", tone: str = "") -> None:
        super().__init__(text, parent)
        self.setProperty("kind", kind)
        self.setProperty("tone", tone)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cache: Optional[QPixmap] = None
        self._cache_key: tuple = ()
        self.restyle()

    @property
    def kind(self) -> str:
        return str(self.property("kind") or "")

    @property
    def tone(self) -> str:
        return str(self.property("tone") or "")

    def restyle(self) -> None:
        size, bold = _BUTTON_FONT.get(self.kind, _BUTTON_FONT[""])
        self.setFont(LOOK.font(size, bold))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        metrics = QFontMetrics(self.font())
        pad_x, pad_y = _BUTTON_PAD.get(self.kind, _BUTTON_PAD[""])
        width = metrics.horizontalAdvance(self.text()) + 2 * pad_x + 2
        height = metrics.height() + 2 * pad_y + 2
        if self.kind == "icon":
            width = max(width, height)
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        # the rendering is cached: during a resize most buttons keep their size and state, so a paint is
        # one pixmap copy instead of a Python-drawn button
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), ratio, self.text(), self.kind, self.tone, self.isEnabled(),
               self.underMouse(), self.isDown(), self.isChecked(), self.hasFocus(), LOOK.theme, LOOK.text_size)
        if key != self._cache_key or self._cache is None:
            pixmap = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            self._render(QPainter(pixmap))
            self._cache, self._cache_key = pixmap, key
        QPainter(self).drawPixmap(0, 0, self._cache)

    def _render(self, painter: QPainter) -> None:
        c = LOOK.colors
        kind, tone = self.kind, self.tone
        enabled, hover, down = self.isEnabled(), self.underMouse(), self.isDown()
        fill: Optional[str]
        border: Optional[str]
        text = _TONE_TEXT.get(tone, "text")
        if kind == "primary":
            fill = ("accent_hover" if hover or down else "accent") if enabled else "surface_hover"
            border = ("text" if self.hasFocus() or down else fill) if enabled else "border"
            text = "on_accent" if enabled else "text_disabled"
        elif kind == "nav":
            fill = "surface_hover" if self.isChecked() else ("surface_alt" if hover else None)
            border = "accent" if self.hasFocus() else None
        elif kind == "link":
            fill, border, text = None, ("accent" if self.hasFocus() else None), "accent_text"
        elif kind == "option":
            fill = "surface_hover" if hover and enabled else "surface"
            border = ("accent" if enabled else "border") if not self.hasFocus() else "text"
            text = "accent_text" if enabled else "text_disabled"
        else:
            fill = "border" if down else ("surface_hover" if hover and enabled else "surface_alt")
            border = "accent" if self.hasFocus() else _TONE_BORDER.get(tone, "border")
        if not enabled and kind not in ("primary", "option"):
            text = "text_disabled"
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(10.0, rect.height() / 2) if kind in ("chip", "option") else 8.0
        painter.setPen(QPen(QColor(c[border]), 1) if border else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c[fill]) if fill else Qt.BrushStyle.NoBrush)
        if fill or border:
            painter.drawRoundedRect(rect, radius, radius)
        font = self.font()
        if kind == "link" and hover:
            font.setUnderline(True)
        painter.setFont(font)
        painter.setPen(QColor(c[text]))
        pad_x, _ = _BUTTON_PAD.get(kind, _BUTTON_PAD[""])
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if kind == "nav"
                                                 else Qt.AlignmentFlag.AlignHCenter)
        area = self.rect().adjusted(pad_x if kind == "nav" else 2, 0, -2, 0)
        painter.drawText(area, align, QFontMetrics(font).elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                                                    area.width()))
        painter.end()


def button(text: str, on_click: Optional[Callable[[], object]] = None, kind: Optional[str] = None,
           tone: Optional[str] = None, tooltip: str = "") -> Button:
    widget = Button(text, kind=kind or "", tone=tone or "")
    if tooltip:
        widget.setToolTip(tooltip)
    if on_click is not None:
        widget.clicked.connect(lambda _checked=False: on_click())
    return widget


# ---------------------------------------------------------------- cards
_CARD = {"true": ("surface", "border", 12), "alt": ("surface_alt", "border", 12), "today": ("surface", "accent", 12),
         "selected": ("surface", "accent", 12), "inset": ("surface_alt", None, 8), "banner": ("banner_bg", None, 10)}


class Card(QFrame):
    """A rounded panel painted from the theme (kind in the ``card`` property)."""

    def __init__(self, kind: str = "true") -> None:
        super().__init__()
        self.setProperty("card", kind)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        fill, border, radius = _CARD.get(str(self.property("card") or "true"), _CARD["true"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(LOOK.color(border), 1) if border else Qt.PenStyle.NoPen)
        painter.setBrush(LOOK.color(fill))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def card(kind: str = "true", vertical: bool = True, margins: tuple[int, int, int, int] = (14, 12, 14, 12),
         spacing: int = 6) -> tuple[Card, QBoxLayout]:
    frame = Card(kind)
    layout: QBoxLayout = QVBoxLayout(frame) if vertical else QHBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


# ---------------------------------------------------------------- layout helpers
def hbox(*widgets: QWidget, spacing: int = 6, margins: tuple[int, int, int, int] = (0, 0, 0, 0),
         stretch_at: Optional[int] = None) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    for i, widget in enumerate(widgets):
        if stretch_at == i:
            layout.addStretch(1)
        layout.addWidget(widget)
    if stretch_at is not None and stretch_at >= len(widgets):
        layout.addStretch(1)
    return layout


def clear_layout(layout: QLayout) -> None:
    """Remove and delete every item of ``layout`` (nested layouts too)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class Dot(QWidget):
    """A small round colour chip (subject colour) with a thin themed outline."""

    def __init__(self, color: str, size: int = 10, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = _subject_color(color)
        self.setFixedSize(size, size)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(LOOK.color("border"), 1))
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))


class ColorBar(QWidget):
    """The thin vertical subject-colour bar at the left of a timeline row."""

    def __init__(self, color: str, width: int = 5) -> None:
        super().__init__()
        self._color = _subject_color(color)
        self.setFixedWidth(width)
        self.setMinimumHeight(24)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawRoundedRect(QRectF(self.rect()), 2.5, 2.5)


class Chip(QWidget):
    """A colour dot followed by bold text."""

    def __init__(self, text: str, color: str, size: int = 12) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(Dot(color), 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = elided(text, "strong14" if size >= 14 else "strong")
        self.label.setMinimumWidth(min(self.label.sizeHint().width(), 60))
        layout.addWidget(self.label, 1)


class Bar(QWidget):
    """A flat backlog bar: ``value`` in 0..1, coloured by tone (success / warning / danger)."""

    def __init__(self, value: float = 0.0, tone: str = "") -> None:
        super().__init__()
        self.value = max(0.0, min(1.0, value))
        self.tone = tone
        self.setFixedHeight(8)
        self.setMinimumWidth(40)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        painter.setBrush(LOOK.color("surface_hover"))
        painter.drawRoundedRect(rect, 4, 4)
        if self.value > 0:
            painter.setBrush(LOOK.color(self.tone if self.tone in ("success", "warning", "danger") else "accent"))
            painter.drawRoundedRect(QRectF(rect.x(), rect.y(), max(8.0, rect.width() * self.value), rect.height()),
                                    4, 4)


class KpiCard(Card):
    def __init__(self, title: str) -> None:
        super().__init__("true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(0)
        layout.addWidget(label(title.upper(), "caption"))
        self.value = label("–", "kpi")
        layout.addWidget(self.value)
        self.hint = elided("", "small")
        layout.addWidget(self.hint)

    def set(self, value: str, hint: str = "", tone: Optional[str] = None) -> None:
        from .style import set_prop
        self.value.setText(value)
        self.hint.setText(hint)
        set_prop(self.value, "tone", tone or "")


def spinbox(value: int = 0, step: int = 15, minimum: int = 0, maximum: int = 960, suffix: str = "") -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setSingleStep(step)
    widget.setValue(int(value))
    widget.setSuffix(suffix)
    widget.setAccelerated(True)
    restyle(widget)
    return widget


def line_edit(text: str = "", placeholder: str = "", password: bool = False) -> QLineEdit:
    widget = QLineEdit(text)
    if placeholder:
        widget.setPlaceholderText(placeholder)
    if password:
        widget.setEchoMode(QLineEdit.EchoMode.Password)
    return widget


def combo(values: list[str], current: str = "", on_change: Optional[Callable[[str], None]] = None) -> QComboBox:
    widget = QComboBox()
    widget.addItems(values)
    if current in values:
        widget.setCurrentText(current)
    if on_change is not None:
        widget.textActivated.connect(on_change)
    return widget


def field(title: str, widget: QWidget) -> QWidget:
    """A caption above a form widget."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    # long captions wrap; short ones elide (cheaper to lay out while a splitter is dragged)
    caption = label(title, "small", wrap=True) if len(title) > 26 else elided(title, "small")
    caption.setBuddy(widget)
    layout.addWidget(caption)
    layout.addWidget(widget)
    return holder


COLUMN_MIN_WIDTH = 220        # Qt: the narrowest column next to a splitter handle (both Subjects columns fit
                              # a 1024 px window, also at the Large text size)


class ScrollArea(QScrollArea):
    """A vertical scroll area that asks for its content's minimum width, so a splitter or the window can
    never make it narrower than its content (nothing is clipped on the right)."""

    floor = 0                     # a column may ask for at least this much (COLUMN_MIN_WIDTH)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        content = self.widget()
        width = content.minimumSizeHint().width() if content is not None else 0
        return QSize(max(self.floor, width + self.verticalScrollBar().sizeHint().width() + 2), 80)


def scroll_area(content: QWidget) -> QScrollArea:
    """A frameless vertical scroll area; it shows the background of the view behind it."""
    area = ScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    area.viewport().setAutoFillBackground(False)
    content.setAutoFillBackground(False)
    area.verticalScrollBar().setSingleStep(24)
    return area


# ---------------------------------------------------------------- splitter
class _Handle(QSplitterHandle):
    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._pressed = False

    def mousePressEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        self._pressed = True
        self.update()
        super().mousePressEvent(event)  # type: ignore[arg-type]

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]

    def mouseDoubleClickEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        splitter = self.splitter()
        if isinstance(splitter, Splitter):
            splitter.reset_requested.emit()

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        if self._pressed or self.underMouse():
            painter = QPainter(self)
            painter.fillRect(self.rect(), LOOK.color("accent" if self._pressed else "border"))


class Splitter(QSplitter):
    """A splitter with live (opaque) resizing whose handle resets the layout on a double click."""
    reset_requested = Signal()

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal) -> None:
        super().__init__(orientation)
        self.setOpaqueResize(True)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)

    def createHandle(self) -> QSplitterHandle:  # noqa: N802 - Qt API
        handle = _Handle(self.orientation(), self)
        handle.setToolTip("Drag to resize, double-click to reset")
        return handle


class Badge(QLabel):
    """The unread counter on the collapsed coach strip."""

    def __init__(self) -> None:
        super().__init__("")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(20, 20))
        self.hide()

    def set_count(self, count: int) -> None:
        self.setText(str(count))
        self.setVisible(count > 0)

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(LOOK.color("danger"))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setPen(LOOK.color("on_danger"))
        painter.setFont(LOOK.font(11, bold=True))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps to the next line (quick chips)."""

    def __init__(self, spacing: int = 6) -> None:
        super().__init__()
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: object) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> object:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> object:  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._arrange(width, apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._arrange(rect.width(), apply=True, origin=(rect.x(), rect.y()))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, width: int, apply: bool, origin: tuple[int, int] = (0, 0)) -> int:
        x, y, line_h = 0, 0, 0
        for item in self._items:
            hint = item.sizeHint()
            if x and x + hint.width() > width:
                x, y, line_h = 0, y + line_h + self._spacing, 0
            if apply:
                item.setGeometry(QRect(origin[0] + x, origin[1] + y, hint.width(), hint.height()))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h
