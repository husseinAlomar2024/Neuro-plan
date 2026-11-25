"""Reviews: due today (with Again/Hard/Good/Easy) and upcoming."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ... import config
from ..widgets import Chip, button, card, clear_layout, label, scroll_area
from . import BaseView

if TYPE_CHECKING:
    from ..app import MainWindow

RATING_TONE = {"again": "dangerline", "hard": "warning", "good": "success", "easy": "accentline"}
RATING_TIP = {"again": "Forgot it: start the review cycle again", "hard": "Remembered with effort",
              "good": "Remembered", "easy": "Remembered easily: the next review comes later"}


class ReviewsView(BaseView):
    title = "Reviews"

    def __init__(self, app: "MainWindow") -> None:
        super().__init__(app)
        self._signature = ""
        self.rating_buttons: dict[int, dict[str, Any]] = {}       # review id → rating → button (tests)
        body = QWidget()
        self.list = QVBoxLayout(body)
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(6)
        self.root.addWidget(scroll_area(body), 1)

    def refresh(self) -> None:
        data = self.app.read("list_reviews")
        if not data:
            return
        signature = json.dumps(data, sort_keys=True)
        if signature == self._signature:
            return
        self._signature = signature
        due, upcoming = data["due"], data["upcoming"]
        self.subtitle.setText(f"{len(due)} due · {len(upcoming)} upcoming")
        clear_layout(self.list)
        self.rating_buttons = {}
        self.list.addWidget(label("DUE TODAY", "caption"))
        if not due:
            self.list.addWidget(label("Nothing to review right now.", "muted"))
        for r in due:
            self.list.addWidget(self._row(r, True))
        self.list.addSpacing(10)
        self.list.addWidget(label("UPCOMING", "caption"))
        if not upcoming:
            self.list.addWidget(label("No upcoming reviews.", "muted"))
        for r in upcoming[:30]:
            self.list.addWidget(self._row(r, False))
        self.list.addStretch(1)

    def _row(self, r: dict[str, Any], due: bool) -> Any:
        frame, layout = card("true" if due else "alt", vertical=False, margins=(12, 8, 10, 8), spacing=8)
        layout.addWidget(Chip(f"{r['subject']} · unit {r['unit']}", r["color"], 13), 0)
        stage = f"stage {r['stage'] + 1}/{len(config.REVIEW_STAGES_DAYS)}"
        if due:
            late = r["days_overdue"]
            when = "due today" if late <= 0 else f"{late} day(s) overdue"
            layout.addWidget(label(f"{stage} · {when}", "small", "warning" if late > 0 else None, wrap=True), 1)
            buttons: dict[str, Any] = {}
            for rating in config.RATINGS:
                widget = button(rating.capitalize(),
                                lambda i=r["id"], x=rating: self.app.call("complete_review", {"id": i, "rating": x}),
                                tone=RATING_TONE.get(rating), tooltip=RATING_TIP.get(rating, ""))
                layout.addWidget(widget)
                buttons[rating] = widget
            self.rating_buttons[r["id"]] = buttons
        else:
            layout.addWidget(label(f"{stage} · due {r['due_date']}", "small", wrap=True), 1)
        return frame
