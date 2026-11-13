"""Small shared Qt widgets and constructors. Styling comes from roles (see style.py), never colours."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QSplitterHandle,
                               QVBoxLayout, QWidget)

from .style import LOOK


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
    return widget


def button(text: str, on_click: Optional[Callable[[], object]] = None, kind: Optional[str] = None,
           tone: Optional[str] = None, tooltip: str = "") -> QPushButton:
    widget = QPushButton(text)
    if kind:
        widget.setProperty("kind", kind)
    if tone:
        widget.setProperty("tone", tone)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        widget.clicked.connect(lambda _checked=False: on_click())
    return widget


def card(kind: str = "true", vertical: bool = True, margins: tuple[int, int, int, int] = (14, 12, 14, 12),
         spacing: int = 6) -> tuple[QFrame, QBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", kind)
    layout: QBoxLayout = QVBoxLayout(frame) if vertical else QHBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


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
        painter.drawEllipse(self.rect().adjusted(0, 0, -1, -1))


class Chip(QWidget):
    """A colour dot followed by bold text."""

    def __init__(self, text: str, color: str, size: int = 12) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(Dot(color), 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = label(text, "strong14" if size >= 14 else "strong")
        layout.addWidget(self.label)
        layout.addStretch(1)


class KpiCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setProperty("card", "true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(0)
        layout.addWidget(label(title.upper(), "caption"))
        self.value = label("–", "kpi")
        layout.addWidget(self.value)
        self.hint = label("", "small")
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
    caption = label(title, "small")
    caption.setBuddy(widget)
    layout.addWidget(caption)
    layout.addWidget(widget)
    return holder


def scroll_area(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    area.verticalScrollBar().setSingleStep(24)
    return area


class _Handle(QSplitterHandle):
    def mouseDoubleClickEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        splitter = self.splitter()
        if isinstance(splitter, Splitter):
            splitter.reset_requested.emit()


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
        self.setObjectName("badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(18, 18))
        self.hide()

    def set_count(self, count: int) -> None:
        self.setText(str(count))
        self.setVisible(count > 0)


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

    def setGeometry(self, rect: object) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._arrange(rect.width(), apply=True, origin=(rect.x(), rect.y()))  # type: ignore[attr-defined]

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
                from PySide6.QtCore import QRect
                item.setGeometry(QRect(origin[0] + x, origin[1] + y, hint.width(), hint.height()))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h


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
        painter.drawRoundedRect(self.rect(), 2.5, 2.5)
