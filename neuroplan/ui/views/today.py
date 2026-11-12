"""Today: KPI cards, pomodoro, and the timeline of today's blocks."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ... import config
from ...timer import TimerCall
from .. import theme
from ..widgets.common import Chip, KpiCard, clear
from ..widgets.pomodoro import PomodoroWidget
from . import BaseView

if TYPE_CHECKING:
    from ..app import NeuroPlanApp


_TASK_INSET = 360        # colour bar, time, paddings and the four action buttons of a pending block
_TASK_INSET_DONE = 220   # the same with a status word instead of buttons


class TodayView(BaseView):
    title = "Today"

    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, app)
        self._signature = ""
        self.header()
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.pack(fill="x", padx=theme.PAD, pady=(0, 8))
        self.kpi_backlog = KpiCard(cards, "Backlog")
        self.kpi_minutes = KpiCard(cards, "Minutes today")
        self.kpi_streak = KpiCard(cards, "Streak")
        self.kpi_reviews = KpiCard(cards, "Reviews due")
        for i, kpi in enumerate((self.kpi_backlog, self.kpi_minutes, self.kpi_streak, self.kpi_reviews)):
            cards.grid_columnconfigure(i, weight=1, uniform="kpi")
            kpi.grid(row=0, column=i, sticky="ew", padx=4)
        self.pomodoro = PomodoroWidget(self, app.timer, self._timer_answer)
        self.pomodoro.pack(fill="x", padx=theme.PAD + 4, pady=(0, 8))
        theme.label(self, "TIMELINE", 10, "bold", theme.MUTED).pack(anchor="w", padx=theme.PAD + 6)
        self.timeline = theme.scrollable(self)
        self.timeline.pack(fill="both", expand=True, padx=theme.PAD - 2, pady=(2, theme.PAD))

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        state = self.app.read("get_state")
        if not state:
            return
        kpis = state["kpis"]
        self.subtitle.configure(text=f"{state['weekday']}, {state['today']} · "
                                     f"{state['today_capacity_min']} min available")
        self.kpi_backlog.set(f"{kpis['backlog_units_total']} units", "behind across subjects",
                             theme.backlog_color(kpis["backlog_units_total"]))
        self.kpi_minutes.set(f"{kpis['done_min_today']} / {kpis['planned_min_today']}", "done / planned")
        self.kpi_streak.set(f"{kpis['streak_days']} days", "in a row")
        self.kpi_reviews.set(str(kpis["reviews_due"]), "spaced-repetition reviews",
                             theme.WARNING if kpis["reviews_due"] else None)
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
        clear(self.timeline)
        if not blocks:
            empty = theme.label(self.timeline, "Nothing is planned for today. Tell the coach how much time you "
                                               "have, or check your availability in Week.", 13, color=theme.MUTED,
                                wraplength=520, justify="left")
            empty.pack(anchor="w", padx=10, pady=20)
            theme.autowrap(empty, self.timeline._parent_canvas, 24)  # noqa: SLF001
            return
        for block in blocks:
            self._row(block)

    def _row(self, b: dict[str, Any]) -> None:
        pending = b["status"] == "pending"
        row = theme.card(self.timeline, fg_color=theme.SURFACE if pending else theme.SURFACE_2)
        row.pack(fill="x", padx=4, pady=4)
        ctk.CTkFrame(row, width=5, height=10, corner_radius=3, fg_color=b["color"]).pack(
            side="left", fill="y", padx=(8, 0), pady=8)
        ctk.CTkLabel(row, text=b["start"] or "—", width=54, font=theme.mono(14, "bold"),
                     text_color=theme.TEXT if pending else theme.MUTED).pack(side="left", padx=(8, 4))
        body = ctk.CTkFrame(row, fg_color="transparent")
        body.pack(side="left", fill="x", expand=True, padx=4, pady=8)
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")
        Chip(top, b["subject"], b["color"], 13).pack(side="left")
        category = "Review" if b["type"] == "REVIEW" else b["category"].capitalize()
        meta = f"{category} · {b['pomodoro']} · {theme.TYPE_LABEL.get(b['type'], b['type'])} · {b['min']} min"
        if b["locked"]:
            meta += " · locked"
        theme.label(top, meta, 11, color=theme.MUTED).pack(side="left", padx=10)
        task = theme.label(body, b["task"], 12, color=theme.TEXT if pending else theme.MUTED, wraplength=430,
                           justify="left", anchor="w")
        task.pack(anchor="w", pady=(3, 0))
        # the task re-wraps with the timeline: the room left by the time column and the buttons
        inset = _TASK_INSET if pending else _TASK_INSET_DONE
        theme.autowrap(task, self.timeline._parent_canvas, inset)  # noqa: SLF001
        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.pack(side="right", padx=8)
        if not pending:
            text = b["status"].capitalize()
            if b["status"] in ("done", "partial") and b["actual_min"] is not None:
                text += f" · {b['actual_min']} min"
            theme.label(actions, text, 12, "bold", theme.STATUS_COLOR.get(b["status"], theme.MUTED)).pack(padx=6)
            return
        block_id = b["id"]
        theme.primary_button(actions, "Start", lambda: self._start(b), width=58, height=28).grid(row=0, column=0, padx=2)
        theme.ghost_button(actions, "Done", lambda: self.app.call("complete_block", {"id": block_id}),
                           width=54, height=28).grid(row=0, column=1, padx=2)
        theme.ghost_button(actions, "Skip", lambda: self.app.call("skip_block", {"id": block_id}),
                           width=50, height=28).grid(row=0, column=2, padx=2)
        theme.ghost_button(actions, "Unlock" if b["locked"] else "Lock",
                           lambda: self.app.call("lock_block", {"id": block_id, "locked": not b["locked"]}),
                           width=60, height=28).grid(row=0, column=3, padx=2)

    # ------------------------------------------------------------ pomodoro
    def _start(self, b: dict[str, Any]) -> None:
        """Start the focus timer for a block. The block is locked so re-planning cannot remove it."""
        if not b["locked"]:
            result = self.app.call("lock_block", {"id": b["id"], "locked": True})
            if not result.ok:
                return
        label = f"{b['subject']} · {theme.TYPE_LABEL.get(b['type'], b['type'])} · unit {b['unit']}"
        self.app.timer.start(b["id"], b["min"], config.BREAK_MIN.get(b["pomodoro"], 5), label)
        self.pomodoro.refresh()

    def _timer_answer(self, call: TimerCall) -> None:
        """Done / Partial / Didn't-study on the confirmation card: only now is anything logged."""
        self.app.call(call.tool, call.args)
