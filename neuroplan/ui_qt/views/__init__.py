"""Centre views: Today, Week, Subjects, Reviews, Settings."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..style import set_bg
from ..widgets import label

if TYPE_CHECKING:
    from ..app import MainWindow


class BaseView(QWidget):
    """A centre view. ``refresh`` re-reads everything through the window's tool calls."""
    title = ""

    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        set_bg(self, "bg")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(16, 16, 16, 10)
        self.root.setSpacing(10)
        self.header_row = QHBoxLayout()
        self.header_row.setSpacing(12)
        self.title_label = label(self.title, "title")
        self.header_row.addWidget(self.title_label)
        self.subtitle: QLabel = label("", "muted")
        self.header_row.addWidget(self.subtitle)
        self.header_row.addStretch(1)
        self.root.addLayout(self.header_row)

    def refresh(self) -> None:
        raise NotImplementedError

    def look_changed(self) -> None:
        """Theme or text size changed; views with painted or cached content override this."""
