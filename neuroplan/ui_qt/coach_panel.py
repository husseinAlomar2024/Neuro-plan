"""The coach panel: message list, typing indicator, quick chips, input. Collapsible to a strip with an
unread badge."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QStackedLayout, QVBoxLayout, QWidget

from .. import config
from ..models import ChatMessage
from .chat import ChatView, Item, MessageWidget
from .widgets import Badge, FlowLayout, button, label

if TYPE_CHECKING:
    from .app import MainWindow

STRIP_WIDTH = 56
QWIDGETSIZE_MAX = 16777215
HISTORY_PAGE = 200            # older messages loaded per step when the list is scrolled to its top


class ChatInput(QPlainTextEdit):
    """Enter sends, Shift+Enter inserts a new line."""
    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class CoachPanel(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        self.setObjectName("coachPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.collapsed = False
        self.unread = 0
        self._saved_sizes: list[int] = []
        self._widgets: dict[int, MessageWidget] = {}          # id(item) → live row widget
        self._oldest_id = 0
        self.has_older = False
        self.stack = QStackedLayout(self)

        # ---- expanded panel
        full = QWidget()
        layout = QVBoxLayout(full)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 0, 0)
        head.addWidget(label("Coach", "h2"))
        self.status = label("", "small")
        head.addWidget(self.status)
        head.addStretch(1)
        self.collapse_button = button("⟩", self.toggle, kind="icon", tooltip="Collapse the coach panel")
        head.addWidget(self.collapse_button)
        layout.addLayout(head)
        self.view = ChatView()
        self.view.verticalScrollBar().valueChanged.connect(self._maybe_load_older)
        layout.addWidget(self.view, 1)
        self.typing = label("", "small")
        self.typing.setContentsMargins(6, 0, 0, 0)
        layout.addWidget(self.typing)
        chips = FlowLayout(6)
        self.chip_buttons = []
        for text in config.QUICK_CHIPS:
            chip = button(text, lambda t=text: self._chip(t), kind="chip")
            chips.addWidget(chip)
            self.chip_buttons.append(chip)
        layout.addLayout(chips)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.input = ChatInput()
        self.input.setPlaceholderText("Message the coach… (Enter sends, Shift+Enter for a new line)")
        self.input.setFixedHeight(64)
        self.input.setTabChangesFocus(True)
        self.input.submitted.connect(self.send)
        bottom.addWidget(self.input, 1)
        self.send_button = button("Send", self.send, kind="primary", tooltip="Send (Enter)")
        self.send_button.setFixedHeight(64)
        bottom.addWidget(self.send_button)
        layout.addLayout(bottom)
        self.stack.addWidget(full)

        # ---- collapsed strip
        strip = QWidget()
        strip.setObjectName("coachStrip")
        strip_layout = QVBoxLayout(strip)
        strip_layout.setContentsMargins(8, 14, 8, 8)
        strip_layout.setSpacing(4)
        self.strip_button = button("💬", self.toggle, kind="icon", tooltip="Open the coach panel")
        self.strip_button.setFixedSize(40, 40)
        strip_layout.addWidget(self.strip_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.badge = Badge()
        strip_layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        strip_layout.addStretch(1)
        self.stack.addWidget(strip)
        self.set_status()

    # ------------------------------------------------------------ collapse / unread
    def toggle(self) -> None:
        splitter = self.app.splitter
        if not self.collapsed:
            self._saved_sizes = splitter.sizes()
            self.collapsed = True
            self.stack.setCurrentIndex(1)
            self.setMinimumWidth(STRIP_WIDTH)
            self.setMaximumWidth(STRIP_WIDTH)
            total = sum(self._saved_sizes)
            splitter.setSizes([total - STRIP_WIDTH, STRIP_WIDTH])
        else:
            self.collapsed = False
            self.stack.setCurrentIndex(0)
            self.setMaximumWidth(QWIDGETSIZE_MAX)
            self.setMinimumWidth(config.COACH_MIN_WIDTH)
            if self._saved_sizes:
                splitter.setSizes(self._saved_sizes)
            self.unread = 0
            QTimer.singleShot(0, self.view.scrollToBottom)
        self.badge.set_count(self.unread if self.collapsed else 0)
        self.app.save_layout()

    def expand(self) -> None:
        if self.collapsed:
            self.toggle()

    def set_status(self) -> None:
        self.status.setText(self.app.coach.status_text())

    def set_busy(self, busy: bool) -> None:
        self.typing.setText("Coach is typing…" if busy else "")
        self.send_button.setEnabled(not busy)

    # ------------------------------------------------------------ input
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.app.coach.submit(text)

    def _chip(self, text: str) -> None:
        if text.endswith("…"):            # "I'm behind in…" needs the student to finish the sentence
            self.input.setPlainText("I'm 2 lectures behind in ")
            cursor = self.input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input.setTextCursor(cursor)
            self.input.setFocus()
            return
        self.app.coach.submit(text)

    # ------------------------------------------------------------ messages
    @property
    def items(self) -> list[Item]:
        return self.view.chat_model.items

    def load_history(self) -> None:
        history = self.app.coach.history(config.CHAT_HISTORY_LIMIT)
        self._oldest_id = history[0].id if history else 0
        self.has_older = len(history) == config.CHAT_HISTORY_LIMIT
        items = [Item.from_message(m) for m in history]
        self._mark_live(items)
        for widget in self._widgets.values():
            widget.deleteLater()
        self._widgets.clear()
        self.view.delegate.invalidate()
        self.view.chat_model.reset(items)
        self.view.relayout(anchor=("bottom", -1, 0))
        self._sync_widgets()
        QTimer.singleShot(0, self.view.scrollToBottom)

    @staticmethod
    def _mark_live(items: list[Item]) -> None:
        """Only the newest coach message has active buttons; Undo sits on the newest action message."""
        last_coach = next((it for it in reversed(items) if it.role == "coach"), None)
        if last_coach is not None:
            last_coach.live = bool(last_coach.confirmations or last_coach.options)
        undo = next((it for it in reversed(items) if it.has_undo_action), None)
        if undo is not None:
            undo.show_undo = True

    def add_message(self, message: ChatMessage, live: bool = True) -> None:
        anchor = self.view.anchor()
        changed: list[Item] = []
        item = Item.from_message(message)
        if item.role != "user":
            changed += self._retire_live()
        if item.role == "coach":
            item.live = bool(item.confirmations or item.options)
            if item.has_undo_action:
                changed += self._drop_undo()
                item.show_undo = True
        for old in changed:
            self.view.delegate.invalidate(old)
        self.view.chat_model.append(item)
        self.view.relayout(anchor=("bottom", -1, 0) if (anchor[0] == "bottom" or item.live) else anchor)
        self._sync_widgets()
        if live and self.collapsed and item.role != "user":
            self.unread += 1
            self.badge.set_count(self.unread)

    def _retire_live(self) -> list[Item]:
        retired = [it for it in self.items if it.live]
        for it in retired:
            it.live = False
        return retired

    def _drop_undo(self) -> list[Item]:
        dropped = [it for it in self.items if it.show_undo]
        for it in dropped:
            it.show_undo = False
        return dropped

    def _sync_widgets(self) -> None:
        """Rows with live buttons get a MessageWidget; others are painted."""
        model = self.view.chat_model
        wanted = {id(it): it for it in model.items if it.needs_widget}
        for key in [k for k in self._widgets if k not in wanted]:
            widget = self._widgets.pop(key)
            row = model.row_of(widget.item)
            if row >= 0:
                self.view.setIndexWidget(model.index(row), None)  # type: ignore[arg-type]
            widget.deleteLater()
        for key, item in wanted.items():
            widget = self._widgets.get(key)
            if widget is None:
                widget = MessageWidget(self.view, item, self._on_row_action)
                self._widgets[key] = widget
                self.view.setIndexWidget(model.index(model.row_of(item)), widget)
            else:
                widget.rebuild()
        self.view.viewport().update()

    def live_buttons(self) -> list[Any]:
        """Every active button inside the message list (tests, keyboard order)."""
        return [b for w in self._widgets.values() for b in w.buttons.values()]

    def _on_row_action(self, item: Item, kind: str, data: Any) -> None:
        if kind == "undo":
            changed = self._drop_undo()
            for it in changed:
                self.view.delegate.invalidate(it)
            self.view.relayout()
            self._sync_widgets()
            self.app.coach.submit_option({"type": "undo"}, "Undo")
            return
        for it in self._retire_live():
            self.view.delegate.invalidate(it)
        self.view.relayout()
        self._sync_widgets()
        if kind == "confirm":
            self.app.coach.submit_option({"type": "confirm", **data}, "Confirm")
        elif kind == "cancel":
            self.app.coach.submit_option({"type": "cancel"}, "Cancel")
        elif kind == "option":
            self.app.coach.submit_option(data["payload"], data.get("label", ""))

    def _maybe_load_older(self, value: int) -> None:
        if value == 0 and self.has_older and self._oldest_id and not self.collapsed:
            QTimer.singleShot(0, self.load_older)

    def load_older(self) -> int:
        """Prepend the next page of older messages, keeping the row at the top edge where it is."""
        if not self.has_older or not self._oldest_id:
            return 0
        older = self.app.coach.history(HISTORY_PAGE, before_id=self._oldest_id)
        self.has_older = len(older) == HISTORY_PAGE
        if not older:
            return 0
        self._oldest_id = older[0].id
        kind, row, offset = self.view.anchor()
        self.view.chat_model.prepend([Item.from_message(m) for m in older])
        self.view.relayout(anchor=(kind, row + len(older), offset) if kind == "row" else ("row", len(older), 0))
        return len(older)

    def look_changed(self) -> None:
        self.view.refresh_all()
        for widget in self._widgets.values():
            widget.rebuild()
