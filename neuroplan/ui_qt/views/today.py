"""Today: KPI cards, pomodoro, and the timeline of today's blocks."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ... import config
from ...timer import TimerCall
from ..widgets import Chip, ColorBar, KpiCard, button, card, clear_layout, elided, label, scroll_area
from . import BaseView
from .pomodoro import PomodoroWidget

if TYPE_CHECKING:
    from ..app import MainWindow

TYPE_LABEL = {"CATCHUP": "Catch-up", "PROGRESS": "Progress", "REVIEW": "Review", "PRACTICE": "Practice"}
STATUS_TONE = {"done": "success", "partial": "warning", "skipped": "muted", "pending": ""}


def backlog_tone(units: int) -> str:
    """Green 0, amber 1-2, red >= 3 (SPEC section 7.3)."""
    if units <= 0:
        return "success"
    return "warning" if units <= 2 else "danger"


class TodayView(BaseView):
    title = "Today"

    def __init__(self, app: "MainWindow") -> None:
        super().__init__(app)
        self._signature = ""
        self.block_rows: dict[int, dict[str, Any]] = {}      # block id → its buttons (tests, focus)
        cards = QGridLayout()
        cards.setHorizontalSpacing(8)
        self.kpi_backlog = KpiCard("Backlog")
        self.kpi_minutes = KpiCard("Minutes today")
        self.kpi_streak = KpiCard("Streak")
        self.kpi_reviews = KpiCard("Reviews due")
        for i, kpi in enumerate((self.kpi_backlog, self.kpi_minutes, self.kpi_streak, self.kpi_reviews)):
            cards.addWidget(kpi, 0, i)
            cards.setColumnStretch(i, 1)
        self.root.addLayout(cards)
        self.pomodoro = PomodoroWidget(app.timer, self._timer_answer)
        self.root.addWidget(self.pomodoro)
        caption = label("TIMELINE", "caption")
        caption.setContentsMargins(4, 4, 0, 0)
        self.root.addWidget(caption)
        self.timeline_body = QWidget()
        self.timeline = QVBoxLayout(self.timeline_body)
        self.timeline.setContentsMargins(0, 0, 4, 0)
        self.timeline.setSpacing(8)
        self.timeline.addStretch(1)
        self.scroll = scroll_area(self.timeline_body)
        self.root.addWidget(self.scroll, 1)

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        state = self.app.read("get_state")
        if not state:
            return
        kpis = state["kpis"]
        self.subtitle.setText(f"{state['weekday']}, {state['today']} · {state['today_capacity_min']} min available")
        self.kpi_backlog.set(f"{kpis['backlog_units_total']} units", "behind across subjects",
                             backlog_tone(kpis["backlog_units_total"]))
        self.kpi_minutes.set(f"{kpis['done_min_today']} / {kpis['planned_min_today']}", "done / planned")
        self.kpi_streak.set(f"{kpis['streak_days']} days", "in a row")
        self.kpi_reviews.set(str(kpis["reviews_due"]), "spaced-repetition reviews",
                             "warning" if kpis["reviews_due"] else None)
        plan = self.app.read("get_plan")
        blocks = plan["days"][0]["blocks"] if plan else []
        signature = json.dumps(blocks, sort_keys=True)
        if signature != self._signature:
            self._signature = signature
            self._render(blocks)
        self.pomodoro.refresh()

    def tick(self) -> None:
        self.pomodoro.refresh()

    def _render(self, blocks: list[dict[str, Any]]) -> None:
        clear_layout(self.timeline)
        self.block_rows = {}
        if not blocks:
            self.timeline.addWidget(label("Nothing is planned for today. Tell the coach how much time you have, "
                                          "or check your availability in Week.", "muted", wrap=True))
        for block in blocks:
            self.timeline.addWidget(self._row(block))
        self.timeline.addStretch(1)

    def _row(self, b: dict[str, Any]) -> QFrame:
        pending = b["status"] == "pending"
        frame, layout = card("true" if pending else "alt", vertical=False, margins=(10, 10, 10, 10), spacing=10)
        layout.addWidget(ColorBar(b["color"]))
        time_label = label(b["start"] or "—", "time", None if pending else "muted")
        time_label.setMinimumWidth(48)
        layout.addWidget(time_label, 0, Qt.AlignmentFlag.AlignVCenter)
        body = QVBoxLayout()
        body.setSpacing(3)
        body.addWidget(Chip(b["subject"], b["color"], 13))
        category = "Review" if b["type"] == "REVIEW" else b["category"].capitalize()
        meta = f"{category} · {b['pomodoro']} · {TYPE_LABEL.get(b['type'], b['type'])} · {b['min']} min"
        if b["locked"]:
            meta += " · locked"
        body.addWidget(elided(meta, "small"))
        body.addWidget(label(b["task"], None if pending else "muted", wrap=True))
        layout.addLayout(body, 1)
        buttons: dict[str, Any] = {}
        if not pending:
            text = b["status"].capitalize()
            if b["status"] in ("done", "partial") and b["actual_min"] is not None:
                text += f" · {b['actual_min']} min"
            status = label(text, "strong", STATUS_TONE.get(b["status"], "muted"))
            layout.addWidget(status, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            block_id = b["id"]
            buttons["start"] = button("Start", lambda: self._start(b), kind="primary",
                                      tooltip="Start the focus timer for this block")
            buttons["done"] = button("Done", lambda: self.app.call("complete_block", {"id": block_id}),
                                     tooltip="Mark the block done without the timer")
            buttons["skip"] = button("Skip", lambda: self.app.call("skip_block", {"id": block_id}),
                                     tooltip="Skip this block; the plan is rebuilt")
            buttons["lock"] = button("Unlock" if b["locked"] else "Lock",
                                     lambda: self.app.call("lock_block", {"id": block_id, "locked": not b["locked"]}),
                                     tooltip="Keep this block where it is when the plan is rebuilt")
            actions = QHBoxLayout()
            actions.setSpacing(4)
            for widget in buttons.values():
                actions.addWidget(widget)
            layout.addLayout(actions)
        self.block_rows[b["id"]] = buttons
        return frame

    # ------------------------------------------------------------ pomodoro
    def _start(self, b: dict[str, Any]) -> None:
        """Start the focus timer for a block. The block is locked so re-planning cannot remove it."""
        if not b["locked"]:
            result = self.app.call("lock_block", {"id": b["id"], "locked": True})
            if not result.ok:
                return
        label_text = f"{b['subject']} · {TYPE_LABEL.get(b['type'], b['type'])} · unit {b['unit']}"
        self.app.timer.start(b["id"], b["min"], config.BREAK_MIN.get(b["pomodoro"], 5), label_text)
        self.pomodoro.refresh()

    def _timer_answer(self, call: TimerCall) -> None:
        """Done / Partial / Didn't-study on the confirmation card: only now is anything logged."""
        self.app.call(call.tool, call.args)
