"""The chat message list: model, layout, painting delegate and the view.

Why a virtual list: only the rows on screen are painted, so 500 messages cost nothing until they are
scrolled into view, and row heights/offsets live in plain Python lists (a QListView asked the Python
delegate for every row's size on each layout pass, which made splitter drags slow). Each row's geometry comes from ``layout_message`` (QTextLayout: bidi-aware, wraps,
slightly looser line spacing) and is cached per width. On a width change only the rows near the viewport
are measured at once; the others keep their previous height and are measured in idle batches, and the
scroll anchor (bottom, or the row at the top edge) is restored after every pass, so nothing jumps.

Rows whose buttons are live (the newest coach message's options or confirm card, and the message that
shows Undo) are shown as a real widget (``MessageWidget``) on top of the row, drawn by the same code,
with real QPushButtons placed where the painted ones would be (keyboard focus, hover, tooltips). All
other rows are painted, their old buttons disabled."""
from __future__ import annotations

import math
from bisect import bisect_right
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QPushButton, QWidget

from ..models import ChatMessage
from .style import LOOK, set_bg
from .widgets import Button

# geometry (logical px, before text scaling)
OUTER_X, OUTER_Y = 6, 4           # around every row
SIDE = 28                         # free space on the other side of a bubble
PAD_X, PAD_Y = 12, 8              # inside a bubble
GAP = 6                           # between sections inside a bubble
LINE_FACTOR = 1.18                # bubble text line spacing (looser than a plain label)
REFINE_BATCH = 12                 # rows measured per idle step after a width change (a few ms)
REFINE_DELAY_MS = 120             # the refinement starts once the width has not changed for this long
NEAR_ROWS = 2                     # rows around the viewport measured at once on a width change


def is_rtl(text: str) -> bool:
    """Direction of the first strong character (Arabic/Hebrew → right-to-left)."""
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return False


@dataclass
class Item:
    """One chat row."""
    id: int
    role: str
    text: str
    proactive: bool = False
    actions: list[dict[str, Any]] = field(default_factory=list)
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    show_undo: bool = False
    live: bool = False                # its option/confirm buttons are still active
    rtl: bool = False

    @staticmethod
    def from_message(message: ChatMessage) -> "Item":
        payload = message.actions or {}
        return Item(message.id, message.role, message.text, bool(message.proactive),
                    list(payload.get("actions") or []), list(payload.get("confirmations") or []),
                    list(payload.get("options") or []), rtl=is_rtl(message.text))

    @property
    def has_undo_action(self) -> bool:
        return any(a.get("ok") and a.get("action_id") and a.get("tool") != "undo_last" for a in self.actions)

    @property
    def needs_widget(self) -> bool:
        return self.show_undo or (self.live and bool(self.confirmations or self.options))


# ---------------------------------------------------------------- text blocks and layout
@dataclass
class Block:
    layout: QTextLayout
    width: int
    height: int
    shift: float                       # x offset so right-aligned lines end at ``width``

    def draw(self, painter: QPainter, x: float, y: float) -> None:
        self.layout.draw(painter, QPointF(x + self.shift, y))


def text_block(text: str, font: QFont, max_width: int, rtl: bool, factor: float = 1.0,
               center: bool = False) -> Block:
    layout = QTextLayout(text.replace("\r\n", "\n").replace("\n", " "), font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    option.setTextDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
    option.setAlignment(Qt.AlignmentFlag.AlignHCenter if center else
                        (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignAbsolute)
    layout.setTextOption(option)
    metrics = QFontMetricsF(font)
    leading = metrics.lineSpacing() * (factor - 1.0)
    width = max(1, max_width)
    y, natural = 0.0, 0.0
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        line.setPosition(QPointF(0, y))
        y += line.height() + leading
        natural = max(natural, line.naturalTextWidth())
    layout.endLayout()
    used = min(width, math.ceil(natural))
    shift = 0.0
    if center:
        shift = -(width - used) / 2
    elif rtl:
        shift = -(width - used)
    return Block(layout, used, math.ceil(max(0.0, y - leading)), shift)


@dataclass
class Element:
    kind: str                  # tag, text, caption, action, undo, card, cardtext, confirm, cancel, option
    rect: QRect
    block: Optional[Block] = None
    data: Any = None


@dataclass
class MessageLayout:
    height: int
    bubble: QRect
    elements: list[Element]
    width: int


def _button_size(text: str, font: QFont) -> QSize:
    metrics = QFontMetricsF(font)
    return QSize(math.ceil(metrics.horizontalAdvance(text)) + 26, math.ceil(metrics.height()) + 10)


def layout_message(item: Item, width: int) -> MessageLayout:
    """Geometry of one row ``width`` px wide (relative to the row's top-left corner)."""
    look = LOOK
    avail = max(60, width - 2 * OUTER_X)
    if item.role == "system_event":
        block = text_block(item.text, look.chat_font(13), avail - 20, item.rtl, center=True)
        rect = QRect(OUTER_X + 10, OUTER_Y + 2, avail - 20, block.height)
        return MessageLayout(block.height + 2 * OUTER_Y + 4, QRect(), [Element("event", rect, block)], width)

    inner_max = max(40, avail - SIDE - 2 * PAD_X)
    parts: list[tuple[str, Any, int, int]] = []          # (kind, data/block, width, height)
    if item.proactive:
        tag_font = look.chat_font(10, bold=True)
        parts.append(("tag", "COACH", math.ceil(QFontMetricsF(tag_font).horizontalAdvance("COACH")),
                      math.ceil(QFontMetricsF(tag_font).height())))
    text = text_block(item.text, look.chat_font(), inner_max, item.rtl, LINE_FACTOR)
    parts.append(("text", text, text.width, text.height))
    if item.actions:
        cap_font = look.chat_font(10, bold=True)
        parts.append(("caption", "ACTIONS EXECUTED",
                      math.ceil(QFontMetricsF(cap_font).horizontalAdvance("ACTIONS EXECUTED")),
                      math.ceil(QFontMetricsF(cap_font).height())))
        for action in item.actions:
            ok = bool(action.get("ok"))
            line = ("✓ " if ok else "✕ ") + str(action.get("summary", ""))
            block = text_block(line, look.chat_font(13), inner_max, is_rtl(str(action.get("summary", ""))))
            parts.append(("action", (block, ok), block.width, block.height))
        if item.show_undo:
            size = _button_size("Undo", look.chat_font(12, bold=True))
            parts.append(("undo", None, size.width() - 8, size.height() - 4))
    for card in item.confirmations:
        preview = str(card.get("preview", "Confirm?"))
        block = text_block(preview, look.chat_font(14, bold=True), inner_max - 16, is_rtl(preview))
        confirm = _button_size("Confirm", look.chat_font(13, bold=True))
        cancel = _button_size("Cancel", look.chat_font(12))
        buttons_w = confirm.width() + cancel.width() + 6
        parts.append(("card", (card, block, confirm, cancel), max(block.width, buttons_w) + 16,
                      8 + block.height + 6 + confirm.height() + 8))
    for option in item.options:
        size = _button_size(str(option.get("label", "")), look.chat_font(12, bold=True))
        parts.append(("option", option, min(size.width(), inner_max), size.height()))

    content_w = min(inner_max, max(p[2] for p in parts))
    elements: list[Element] = []
    y = PAD_Y
    last_gap = 0
    for kind, data, w, h in parts:
        if kind == "tag":
            elements.append(Element("tag", QRect(PAD_X, y, w, h), data=data))
            y += h + 2
            last_gap = 2
            continue
        if kind == "text":
            x = PAD_X + (content_w - w if item.rtl else 0)
            elements.append(Element("text", QRect(x, y, w, h), data))
            y += h + GAP
            last_gap = GAP
            continue
        if kind == "caption":
            elements.append(Element("caption", QRect(PAD_X, y, w, h), data=data))
            y += h + 2
            last_gap = 2
            continue
        if kind == "action":
            block, ok = data
            elements.append(Element("action", QRect(PAD_X, y, block.width, block.height), block, ok))
            y += h + 2
            last_gap = 2
            continue
        if kind == "undo":
            elements.append(Element("undo", QRect(PAD_X - 4, y, w, h)))
            y += h + GAP
            last_gap = GAP
            continue
        if kind == "card":
            card, block, confirm, cancel = data
            box = QRect(PAD_X, y, content_w, h)
            elements.append(Element("card", box, data=card))
            elements.append(Element("cardtext", QRect(PAD_X + 8, y + 8, block.width, block.height), block))
            by = y + 8 + block.height + 6
            elements.append(Element("confirm", QRect(PAD_X + 8, by, confirm.width(), confirm.height()), data=card))
            elements.append(Element("cancel", QRect(PAD_X + 8 + confirm.width() + 6, by, cancel.width(),
                                                    cancel.height()), data=card))
            y += h + GAP
            last_gap = GAP
            continue
        if kind == "option":
            elements.append(Element("option", QRect(PAD_X, y, w, h), data=data))
            y += h + 4
            last_gap = 4
    bubble_h = y - last_gap + PAD_Y
    bubble_w = content_w + 2 * PAD_X
    left = OUTER_X + (avail - bubble_w if item.role == "user" else 0)
    for element in elements:
        element.rect.translate(left, OUTER_Y)
    return MessageLayout(bubble_h + 2 * OUTER_Y, QRect(left, OUTER_Y, bubble_w, bubble_h), elements, width)


# ---------------------------------------------------------------- painting
def paint_message(painter: QPainter, origin: QPoint, item: Item, layout: MessageLayout,
                  skip_buttons: bool = False) -> None:
    """Draw ``item`` at ``origin``; with ``skip_buttons`` the real buttons of a MessageWidget cover them."""
    look = LOOK
    painter.save()
    painter.translate(origin)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if item.role == "system_event":
        painter.setPen(look.color("text_muted"))
        element = layout.elements[0]
        element.block.draw(painter, element.rect.x(), element.rect.y())  # type: ignore[union-attr]
        painter.restore()
        return
    fill = look.color("user_bubble" if item.role == "user" else "coach_bubble")
    edge = look.color("user_bubble" if item.role == "user" else "coach_bubble_border")
    painter.setPen(QPen(edge, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(QRectF(layout.bubble).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
    for element in layout.elements:
        rect = element.rect
        if element.kind == "tag":
            painter.setFont(look.chat_font(10, bold=True))
            painter.setPen(look.color("accent_text"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, element.data)
        elif element.kind == "text":
            painter.setPen(look.color("text"))
            element.block.draw(painter, rect.x(), rect.y())  # type: ignore[union-attr]
        elif element.kind == "caption":
            painter.setFont(look.chat_font(10, bold=True))
            painter.setPen(look.color("text_muted"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, element.data)
        elif element.kind == "action":
            painter.setPen(look.color("success" if element.data else "danger"))
            element.block.draw(painter, rect.x(), rect.y())  # type: ignore[union-attr]
        elif element.kind == "undo" and not skip_buttons:
            painter.setFont(look.chat_font(12, bold=True))
            painter.setPen(look.color("accent_text"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Undo")
        elif element.kind == "card":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.color("surface_hover"))
            painter.drawRoundedRect(QRectF(rect), 8, 8)
        elif element.kind == "cardtext":
            painter.setPen(look.color("text"))
            element.block.draw(painter, rect.x(), rect.y())  # type: ignore[union-attr]
        elif element.kind in ("confirm", "cancel", "option") and not skip_buttons:
            _paint_disabled_button(painter, rect, element)
    painter.restore()


def _paint_disabled_button(painter: QPainter, rect: QRect, element: Element) -> None:
    look = LOOK
    text = {"confirm": "Confirm", "cancel": "Cancel"}.get(element.kind) or str(element.data.get("label", ""))
    radius = rect.height() / 2 if element.kind == "option" else 8
    painter.setPen(QPen(look.color("border"), 1))
    painter.setBrush(look.color("surface_alt") if element.kind != "option" else look.color("surface"))
    painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.setPen(look.color("text_disabled"))
    painter.setFont(look.chat_font(12, bold=element.kind != "cancel"))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

# ---------------------------------------------------------------- model
class ChatModel:
    """The rows of the chat (plain Python list; the view keeps its own geometry)."""

    def __init__(self) -> None:
        self.items: list[Item] = []

    def rowCount(self) -> int:  # noqa: N802 - kept from the Qt model API
        return len(self.items)

    def append(self, item: Item) -> None:
        self.items.append(item)

    def prepend(self, items: list[Item]) -> None:
        self.items[0:0] = items

    def reset(self, items: list[Item]) -> None:
        self.items = list(items)

    def row_of(self, item: Item) -> int:
        for row in range(len(self.items) - 1, -1, -1):
            if self.items[row] is item:
                return row
        return -1


# ---------------------------------------------------------------- layout cache
class LayoutCache:
    """Row layouts cached by (item, width). On a width change only ``exact_rows`` are measured; the others
    keep their previous height as an estimate (``stale``) until the idle refinement measures them."""

    def __init__(self) -> None:
        self.width = 0
        self._cache: dict[tuple[int, int], MessageLayout] = {}      # (id(item), width) → layout
        self._last: dict[int, int] = {}                             # id(item) → last measured height
        self.stale: set[int] = set()
        self.exact_rows: Optional[range] = None

    def invalidate(self, item: Optional[Item] = None) -> None:
        if item is None:
            self._cache.clear()
            self._last.clear()
            self.stale.clear()
            return
        key = id(item)
        for cache_key in [k for k in self._cache if k[0] == key]:
            del self._cache[cache_key]
        self._last.pop(key, None)

    def layout_for(self, item: Item) -> MessageLayout:
        key = (id(item), self.width)
        layout = self._cache.get(key)
        if layout is None:
            layout = layout_message(item, self.width)
            if len(self._cache) > 4000:
                self._cache.clear()
            self._cache[key] = layout
            self._last[id(item)] = layout.height
            self.stale.discard(id(item))
        return layout

    def height_for(self, row: int, item: Item) -> int:
        cached = self._cache.get((id(item), self.width))
        if cached is not None:
            return cached.height
        if self.exact_rows is not None and row not in self.exact_rows and id(item) in self._last:
            self.stale.add(id(item))
            return self._last[id(item)]                # estimate until the idle refinement
        return self.layout_for(item).height


# ---------------------------------------------------------------- live row
# ---------------------------------------------------------------- live row
class MessageWidget(QWidget):
    """A row with active buttons: painted like the others, with real buttons on top."""

    def __init__(self, view: "ChatView", item: Item, on_action: Callable[[Item, str, Any], None]) -> None:
        super().__init__(view.viewport())
        self.view = view
        self.item = item
        self.on_action = on_action
        self.buttons: dict[int, QPushButton] = {}           # element index → button
        self._layout: Optional[MessageLayout] = None
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.rebuild()

    def rebuild(self) -> None:
        for btn in self.buttons.values():
            btn.deleteLater()
        self.buttons = {}
        layout = self.view.delegate.layout_for(self.item)
        self._layout = layout
        for n, element in enumerate(layout.elements):
            if element.kind == "undo":
                btn = Button("Undo", self, kind="link")
                btn.setToolTip("Undo the last change")
                btn.clicked.connect(lambda: self.on_action(self.item, "undo", None))
            elif element.kind == "confirm" and self.item.live:
                btn = Button("Confirm", self, kind="primary")
                btn.clicked.connect(lambda _=False, c=element.data: self.on_action(self.item, "confirm", c))
            elif element.kind == "cancel" and self.item.live:
                btn = Button("Cancel", self)
                btn.clicked.connect(lambda _=False, c=element.data: self.on_action(self.item, "cancel", c))
            elif element.kind == "option" and self.item.live:
                btn = Button(str(element.data.get("label", "")), self, kind="option")
                btn.clicked.connect(lambda _=False, o=element.data: self.on_action(self.item, "option", o))
            else:
                continue
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setGeometry(element.rect)
            btn.show()
            self.buttons[n] = btn
        self.update()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        layout = self.view.delegate.layout_for(self.item)
        if layout is not self._layout:
            old = self._layout
            if old is None or [e.kind for e in old.elements] != [e.kind for e in layout.elements]:
                self.rebuild()                       # different sections (Undo added/removed …)
            else:
                self._layout = layout
                for n, btn in self.buttons.items():
                    btn.setGeometry(layout.elements[n].rect)
        super().resizeEvent(event)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt API
        if self._layout is None:
            return
        painter = QPainter(self)
        paint_message(painter, QPoint(0, 0), self.item, self._layout, skip_buttons=True)
        # disabled (retired) buttons of a row that is only live for Undo
        if not self.item.live:
            for element in self._layout.elements:
                if element.kind in ("confirm", "cancel", "option"):
                    _paint_disabled_button(painter, element.rect, element)




# ---------------------------------------------------------------- view
class ChatView(QAbstractScrollArea):
    """A virtual list: row heights and running offsets live in Python, only visible rows are painted,
    scrolling blits the viewport, live rows (MessageWidget) are positioned by the view itself."""

    def __init__(self) -> None:
        super().__init__()
        self.chat_model = ChatModel()
        self.delegate = LayoutCache()
        self._offsets: list[int] = [0]                 # top of every row, plus the total height at the end
        self._widgets: dict[int, QWidget] = {}          # id(item) → live row widget
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalScrollBar().setSingleStep(24)
        set_bg(self, "bg")
        # measuring off-screen rows waits until a resize pauses, then runs in small idle batches
        self._refine = QTimer(self, singleShot=True, interval=REFINE_DELAY_MS, timeout=self._refine_step)

    # ------------------------------------------------------------ geometry
    @property
    def total_height(self) -> int:
        return self._offsets[-1]

    def doItemsLayout(self) -> None:  # noqa: N802 - kept from the QListView API
        """Recompute every row's offset (cached heights; estimates for stale rows) and the scroll range."""
        offsets = [0]
        total = 0
        height_for = self.delegate.height_for
        for row, item in enumerate(self.chat_model.items):
            total += height_for(row, item)
            offsets.append(total)
        self._offsets = offsets
        bar = self.verticalScrollBar()
        page = self.viewport().height()
        bar.setPageStep(max(1, page))
        bar.setRange(0, max(0, total - page))
        self._place_widgets()
        self.viewport().update()

    def visual_rect(self, row: int) -> QRect:
        top = self._offsets[row] - self.verticalScrollBar().value()
        return QRect(0, top, self.viewport().width(), self._offsets[row + 1] - self._offsets[row])

    def row_at(self, y: int) -> int:
        """The row at viewport y, or -1."""
        content_y = y + self.verticalScrollBar().value()
        if not self.chat_model.items or content_y < 0 or content_y >= self.total_height:
            return -1
        return max(0, bisect_right(self._offsets, content_y) - 1)

    def scroll_to_row(self, row: int) -> None:
        """Put ``row`` at the top edge (as far as the content allows)."""
        self.verticalScrollBar().setValue(self._offsets[row])

    def scrollToBottom(self) -> None:  # noqa: N802 - Qt-style name used by the panel
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------ live row widgets
    def set_row_widget(self, item: Item, widget: Optional[QWidget]) -> None:
        old = self._widgets.pop(id(item), None)
        if old is not None and old is not widget:
            old.hide()
        if widget is not None:
            self._widgets[id(item)] = widget
            widget.show()
        self._place_widgets()

    def row_widget(self, item: Item) -> Optional[QWidget]:
        return self._widgets.get(id(item))

    def _place_widgets(self) -> None:
        if not self._widgets:
            return
        rows = {id(item): row for row, item in enumerate(self.chat_model.items) if id(item) in self._widgets}
        for key, widget in self._widgets.items():
            row = rows.get(key)
            if row is not None:
                widget.setGeometry(self.visual_rect(row))

    # ------------------------------------------------------------ painting and scrolling
    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        items = self.chat_model.items
        if not items:
            return
        painter = QPainter(self.viewport())
        clip = event.rect()
        scroll = self.verticalScrollBar().value()
        row = max(0, bisect_right(self._offsets, scroll + clip.top()) - 1)
        bottom = scroll + clip.bottom()
        while row < len(items) and self._offsets[row] <= bottom:
            item = items[row]
            if id(item) not in self._widgets:
                paint_message(painter, QPoint(0, self._offsets[row] - scroll), item, self.delegate.layout_for(item))
            row += 1
        painter.end()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - Qt API
        self.viewport().scroll(dx, dy)                  # blit; moves the live row widgets too

    # ------------------------------------------------------------ anchor
    def at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 2

    def anchor(self) -> tuple[str, int, int]:
        if self.at_bottom() or not self.chat_model.items:
            return ("bottom", -1, 0)
        row = self.row_at(1)
        if row < 0:
            return ("bottom", -1, 0)
        return ("row", row, self.visual_rect(row).top())

    def restore(self, anchor: tuple[str, int, int]) -> None:
        kind, row, offset = anchor
        if kind == "bottom" or row < 0 or row >= self.chat_model.rowCount():
            self.scrollToBottom()
        else:
            self.verticalScrollBar().setValue(self._offsets[row] - offset)
        self._place_widgets()

    def visible_rows(self) -> range:
        last = self.chat_model.rowCount() - 1
        top = self.row_at(1)
        bottom = self.row_at(max(1, self.viewport().height() - 2))
        first_row = top if top >= 0 else max(0, last - 20)
        last_row = bottom if bottom >= 0 else last
        return range(max(0, first_row - NEAR_ROWS), min(last, last_row + NEAR_ROWS) + 1)

    # ------------------------------------------------------------ width changes
    def relayout(self, width: Optional[int] = None, anchor: Optional[tuple[str, int, int]] = None) -> None:
        """Lay the rows out for the viewport width (or ``width``), keeping the anchor."""
        anchor = anchor or self.anchor()
        new_width = width if width is not None else self.viewport().width()
        if new_width != self.delegate.width:
            self.delegate.exact_rows = self.visible_rows() if self.delegate.width else None
            self.delegate.width = new_width
        self.doItemsLayout()
        self.delegate.exact_rows = None
        self.restore(anchor)
        if self.delegate.stale:
            self._refine.start(REFINE_DELAY_MS)       # restarted by every width change: waits for a pause

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        anchor = self.anchor()
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width() or self.delegate.width == 0:
            self.relayout(anchor=anchor)
        else:
            bar = self.verticalScrollBar()
            page = self.viewport().height()
            bar.setPageStep(max(1, page))
            bar.setRange(0, max(0, self.total_height - page))
            self.restore(anchor)

    def _refine_step(self) -> None:
        """Measure the next batch of rows that still carry an estimated height, keeping the anchor."""
        stale = self.delegate.stale
        if not stale:
            return
        anchor = self.anchor()
        done = 0
        for item in self.chat_model.items:
            if id(item) in stale:
                self.delegate.layout_for(item)
                done += 1
                if done >= REFINE_BATCH:
                    break
        self.doItemsLayout()
        self.restore(anchor)
        if self.delegate.stale:
            self._refine.start(0)

    def refresh_all(self) -> None:
        """Text size or theme changed: re-measure everything now."""
        anchor = self.anchor()
        self.delegate.invalidate()
        self.doItemsLayout()
        self.restore(anchor)
        self.viewport().update()
