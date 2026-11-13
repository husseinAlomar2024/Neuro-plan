"""Week: risk banner, availability editor with a today-only override, and the 7-column board."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ... import config
from ..style import LOOK
from ..widgets import _subject_color, button, card, clear_layout, combo, label, line_edit, scroll_area, spinbox
from . import BaseView

if TYPE_CHECKING:
    from ..app import MainWindow

ABBR = {"CATCHUP": "catch-up", "PROGRESS": "progress", "REVIEW": "review", "PRACTICE": "practice"}
MARK = {"done": "✓ ", "partial": "◐ ", "skipped": "✕ ", "pending": ""}


def short_name(name: str) -> str:
    if len(name) <= 14:
        return name
    return "".join(w[0] for w in name.split()).upper() if " " in name else name[:13] + "…"


class BlockChip(QWidget):
    """A block on the board: subject colour, two short lines (painted, so theme and size stay current)."""

    def __init__(self, block: dict[str, Any]) -> None:
        super().__init__()
        self.block = block
        self.lines = (f"{MARK.get(block['status'], '')}{block['start']} {short_name(block['subject'])}",
                      f"{ABBR.get(block['type'], '')} · u{block['unit']}")
        self.setToolTip(f"{block['subject']} · {ABBR.get(block['type'], '')} · unit {block['unit']} · "
                        f"{block['min']} min · {block['status']}")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        metrics = QFontMetrics(LOOK.font(10, bold=True))
        return QSize(60, 2 * metrics.height() + 8)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(40, self.sizeHint().height())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_subject_color(self.block["color"]))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        font = LOOK.font(10, bold=True)
        painter.setFont(font)
        token = "chip_text" if self.block["status"] != "skipped" else "chip_text_muted"
        painter.setPen(QColor(LOOK.colors[token]))
        metrics = QFontMetrics(font)
        width = self.width() - 12
        for i, line in enumerate(self.lines):
            painter.drawText(6, 4 + metrics.ascent() + i * metrics.height(),
                             metrics.elidedText(line, Qt.TextElideMode.ElideRight, width))


class WeekView(BaseView):
    title = "Week"

    def __init__(self, app: "MainWindow") -> None:
        super().__init__(app)
        self._board_signature = ""
        self._availability_signature = ""
        self.rebalance_button = button("Rebalance", lambda: app.call("regenerate_plan", {}),
                                       tooltip="Rebuild the plan from the current data")
        self.why_button = button("Why this order?", lambda: app.ask_coach("Why this order?"),
                                 tooltip="Ask the coach to explain the priorities")
        self.header_row.addWidget(self.rebalance_button)
        self.header_row.addWidget(self.why_button)

        self.banner, banner_layout = card("banner", vertical=False, margins=(12, 8, 10, 8), spacing=10)
        self.banner_text = label("", wrap=True)
        banner_layout.addWidget(self.banner_text, 1)
        banner_layout.addWidget(button("See options", lambda: app.ask_coach("Am I on track?"),
                                       tooltip="Ask the coach for the options"))
        self.banner.hide()
        self.root.addWidget(self.banner)

        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(10)
        content.addWidget(self._build_editor())
        self.board_widget = QWidget()
        self.board = QGridLayout(self.board_widget)
        self.board.setContentsMargins(0, 0, 0, 0)
        self.board.setHorizontalSpacing(6)
        for i in range(7):
            self.board.setColumnStretch(i, 1)
        content.addWidget(self.board_widget)
        content.addStretch(1)
        self.root.addWidget(scroll_area(body), 1)

    def _build_editor(self) -> QFrame:
        frame, layout = card(margins=(14, 10, 14, 12), spacing=8)
        layout.addWidget(label("AVAILABILITY", "caption"))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(5)
        layout.addLayout(self.grid)
        self.rows: dict[int, tuple[Any, Any, Any]] = {}
        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.save_week_button = button("Save week", self._save_week, kind="primary",
                                       tooltip="Save the weekly availability; the plan is rebuilt")
        footer.addWidget(self.save_week_button)
        footer.addSpacing(16)
        footer.addWidget(label("Today only:", "muted"))
        self.override = spinbox(120, step=15, maximum=config.MAX_DAY_MINUTES, suffix=" min")
        footer.addWidget(self.override)
        self.apply_button = button("Apply", self._apply_override, tooltip="Use this many minutes today only")
        self.clear_button = button("Clear", self._clear_override, tooltip="Back to the weekly minutes for today")
        footer.addWidget(self.apply_button)
        footer.addWidget(self.clear_button)
        self.override_note = label("", "small", wrap=True)
        footer.addWidget(self.override_note, 1)
        layout.addLayout(footer)
        return frame

    # ------------------------------------------------------------ helpers
    def _order(self) -> list[int]:
        start = 6 if self.app.setting("week_start") != "mon" else 0
        return [(start + i) % 7 for i in range(7)]

    def _week_start(self, today: date) -> date:
        first = self._order()[0]
        return today - timedelta(days=(today.weekday() - first) % 7)

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        today = self.app.today()
        risks = self.app.read("get_risks").get("risks", [])
        if risks:
            r = risks[0]
            more = f" (+{len(risks) - 1} more)" if len(risks) > 1 else ""
            self.banner_text.setText(f"⚠ {r['title']} on {r['date']}: the plan is {r['shortfall_min']} min "
                                     f"short{more}.")
            self.banner.show()
        else:
            self.banner.hide()
        self._refresh_availability(today)
        start = self._week_start(today)
        plan = self.app.read("get_plan", {"date_from": start.isoformat(),
                                          "date_to": (start + timedelta(days=6)).isoformat()})
        days = plan.get("days", [])
        signature = json.dumps(days, sort_keys=True) + today.isoformat()
        if signature != self._board_signature:
            self._board_signature = signature
            self._render_board(days, today)
        self.subtitle.setText(f"{start.strftime('%d %b')} – {(start + timedelta(days=6)).strftime('%d %b')}")

    def _refresh_availability(self, today: date) -> None:
        data = self.app.read("get_availability")
        if not data:
            return
        signature = json.dumps(data, sort_keys=True) + "".join(map(str, self._order()))
        if signature == self._availability_signature:
            return
        self._availability_signature = signature
        clear_layout(self.grid)
        self.rows.clear()
        by_day = {d["weekday"]: d for d in data["days"]}
        for col, title in enumerate(("Day", "Minutes", "Start", "Peak")):
            self.grid.addWidget(label(title, "small"), 0, col)
            self.grid.addWidget(label(title, "small"), 0, col + 5)
        self.grid.setColumnMinimumWidth(4, 24)
        for i, wd in enumerate(self._order()):
            d = by_day[wd]
            base = 0 if i < 4 else 5
            row = 1 + (i if i < 4 else i - 4)
            self.grid.addWidget(label(config.WEEKDAY_LONG[wd], "strong"), row, base)
            spin = spinbox(d["minutes"], step=15, maximum=config.MAX_DAY_MINUTES)
            start = line_edit(d["start_time"], "HH:MM")
            start.setMaximumWidth(80)
            peak = combo(list(config.PEAK_WINDOWS), d["peak_window"])
            self.grid.addWidget(spin, row, base + 1)
            self.grid.addWidget(start, row, base + 2)
            self.grid.addWidget(peak, row, base + 3)
            self.rows[wd] = (spin, start, peak)
        override = next((o for o in data["overrides"] if o["date"] == today.isoformat()), None)
        if override:
            self.override.setValue(override["minutes"])
            self.override_note.setText(f"override active: {override['minutes']} min today")
        else:
            self.override.setValue(by_day[today.weekday()]["minutes"])
            self.override_note.setText("")

    def _save_week(self) -> None:
        days = [{"weekday": wd, "minutes": spin.value(), "start_time": start.text().strip(),
                 "peak_window": peak.currentText()} for wd, (spin, start, peak) in self.rows.items()]
        self._availability_signature = ""
        self.app.call("set_weekly_availability", {"days": days})

    def _apply_override(self) -> None:
        self._availability_signature = ""
        self.app.call("set_day_override", {"date": self.app.today().isoformat(), "minutes": self.override.value()})

    def _clear_override(self) -> None:
        self._availability_signature = ""
        self.app.call("set_day_override", {"date": self.app.today().isoformat(), "clear": True})

    # ------------------------------------------------------------ board
    def _render_board(self, days: list[dict[str, Any]], today: date) -> None:
        clear_layout(self.board)
        for col, day in enumerate(days):
            d = date.fromisoformat(day["date"])
            is_today = d == today
            column, layout = card("today" if is_today else "alt", margins=(8, 8, 8, 8), spacing=4)
            layout.addWidget(label(f"{day['weekday'][:3]} {d.day}", "strong", "accent" if is_today else None))
            planned = sum(b["min"] for b in day["blocks"] if b["status"] != "skipped")
            layout.addWidget(label(f"{planned} / {day['capacity_min']} min", "small"))
            if not day["blocks"]:
                layout.addWidget(label("—", "muted"))
            for b in day["blocks"]:
                layout.addWidget(BlockChip(b))
            layout.addStretch(1)
            self.board.addWidget(column, 0, col)
            self.board.setColumnStretch(col, 1)
