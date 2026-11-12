"""Reviews: due today (with Again/Hard/Good/Easy) and upcoming."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ... import config
from .. import theme
from ..widgets.common import Chip, clear
from . import BaseView

if TYPE_CHECKING:
    from ..app import NeuroPlanApp

_RATING_COLOR = {"again": theme.DANGER, "hard": theme.WARNING, "good": theme.SUCCESS,
                 "easy": theme.ACCENT_TEXT}


class ReviewsView(BaseView):
    title = "Reviews"

    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, app)
        self._signature = ""
        self.header()
        self.list = theme.scrollable(self)
        self.list.pack(fill="both", expand=True, padx=theme.PAD - 2, pady=(0, theme.PAD))

    def refresh(self) -> None:
        data = self.app.read("list_reviews")
        if not data:
            return
        signature = json.dumps(data, sort_keys=True)
        if signature == self._signature:
            return
        self._signature = signature
        due, upcoming = data["due"], data["upcoming"]
        self.subtitle.configure(text=f"{len(due)} due · {len(upcoming)} upcoming")
        clear(self.list)
        theme.label(self.list, "DUE TODAY", 10, "bold", theme.MUTED).pack(anchor="w", padx=8, pady=(4, 2))
        if not due:
            theme.label(self.list, "Nothing to review right now.", 13, color=theme.MUTED).pack(
                anchor="w", padx=10, pady=(4, 12))
        for r in due:
            self._row(r, True)
        theme.label(self.list, "UPCOMING", 10, "bold", theme.MUTED).pack(anchor="w", padx=8, pady=(14, 2))
        if not upcoming:
            theme.label(self.list, "No upcoming reviews.", 13, color=theme.MUTED).pack(anchor="w", padx=10, pady=4)
        for r in upcoming[:30]:
            self._row(r, False)

    def _row(self, r: dict[str, Any], due: bool) -> None:
        row = theme.card(self.list, fg_color=theme.SURFACE if due else theme.SURFACE_2)
        row.pack(fill="x", padx=4, pady=3)
        Chip(row, f"{r['subject']} · unit {r['unit']}", r["color"], 13).pack(side="left", padx=12, pady=10)
        stage = f"stage {r['stage'] + 1}/{len(config.REVIEW_STAGES_DAYS)}"
        if due:
            late = r["days_overdue"]
            when = "due today" if late <= 0 else f"{late} day(s) overdue"
            theme.label(row, f"{stage} · {when}", 11, color=theme.WARNING if late > 0 else theme.MUTED).pack(
                side="left", padx=6)
            for rating in reversed(config.RATINGS):
                ctk.CTkButton(row, text=rating.capitalize(), width=58, height=28, corner_radius=8,
                              fg_color=theme.SURFACE_2, hover_color=theme.SURFACE_3, border_width=1,
                              border_color=_RATING_COLOR[rating], text_color=_RATING_COLOR[rating],
                              font=theme.font(12, "bold"),
                              command=lambda i=r["id"], x=rating: self.app.call(
                                  "complete_review", {"id": i, "rating": x})).pack(side="right", padx=3, pady=8)
        else:
            theme.label(row, f"{stage} · due {r['due_date']}", 11, color=theme.MUTED).pack(side="left", padx=6)
