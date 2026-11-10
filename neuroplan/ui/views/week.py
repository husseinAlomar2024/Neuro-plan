"""Week: risk banner, availability editor with a today-only override, and the 7-column board."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ... import config
from .. import theme
from ..widgets.common import Spinbox, clear, entry, option_menu
from . import BaseView

if TYPE_CHECKING:
    from ..app import NeuroPlanApp

_ABBR = {"CATCHUP": "catch-up", "PROGRESS": "progress", "REVIEW": "review", "PRACTICE": "practice"}
_MARK = {"done": "✓ ", "partial": "◐ ", "skipped": "✕ ", "pending": ""}


class WeekView(BaseView):
    title = "Week"

    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, app)
        self._board_signature = ""
        self._availability_signature = ""
        bar = self.header()
        theme.ghost_button(bar, "Why this order?", lambda: app.ask_coach("Why this order?"), width=130
                           ).pack(side="right")
        theme.ghost_button(bar, "Rebalance", lambda: app.call("regenerate_plan", {}), width=96
                           ).pack(side="right", padx=8)

        self.banner_holder = ctk.CTkFrame(self, fg_color="transparent", height=1)
        self.banner_holder.pack(fill="x", padx=theme.PAD + 2)
        self.banner = ctk.CTkFrame(self.banner_holder, fg_color=("#FBE9E7", "#3A2224"), corner_radius=10)
        self.banner_text = theme.label(self.banner, "", 12, "bold", theme.DANGER, wraplength=560, justify="left")
        self.banner_text.pack(side="left", padx=12, pady=8)
        theme.ghost_button(self.banner, "See options", lambda: app.ask_coach("Am I on track?"), width=100
                           ).pack(side="right", padx=10)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=theme.PAD - 4, pady=(0, theme.PAD))
        self.body = body

        # ---- availability editor
        editor = theme.card(body)
        editor.pack(fill="x", padx=6, pady=(4, 10))
        theme.label(editor, "AVAILABILITY", 10, "bold", theme.MUTED).grid(
            row=0, column=0, columnspan=8, sticky="w", padx=14, pady=(10, 2))
        self.rows: dict[int, tuple[Spinbox, ctk.StringVar, ctk.StringVar]] = {}
        self.day_labels: dict[int, ctk.CTkLabel] = {}
        self.grid_holder = ctk.CTkFrame(editor, fg_color="transparent")
        self.grid_holder.grid(row=1, column=0, columnspan=8, sticky="ew", padx=10)
        footer = ctk.CTkFrame(editor, fg_color="transparent")
        footer.grid(row=2, column=0, columnspan=8, sticky="ew", padx=10, pady=(6, 12))
        theme.primary_button(footer, "Save week", self._save_week, width=100).pack(side="left", padx=4)
        theme.label(footer, "Today only:", 12, color=theme.MUTED).pack(side="left", padx=(24, 6))
        self.override = Spinbox(footer, 120, step=15)
        self.override.pack(side="left")
        theme.ghost_button(footer, "Apply", self._apply_override, width=64).pack(side="left", padx=6)
        theme.ghost_button(footer, "Clear", self._clear_override, width=60).pack(side="left")
        self.override_note = theme.label(footer, "", 11, color=theme.MUTED)
        self.override_note.pack(side="left", padx=10)

        # ---- board
        self.board = ctk.CTkFrame(body, fg_color="transparent")
        self.board.pack(fill="both", expand=True, padx=2)
        for i in range(7):
            self.board.grid_columnconfigure(i, weight=1, uniform="day")

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
            self.banner_text.configure(text=f"⚠ {r['title']} on {r['date']}: the plan is {r['shortfall_min']} min "
                                            f"short{more}.")
            self.banner.pack(fill="x", pady=(0, 6))
        else:
            self.banner.pack_forget()
            self.banner_holder.configure(height=1)
        self._refresh_availability(today)
        start = self._week_start(today)
        plan = self.app.read("get_plan", {"date_from": start.isoformat(),
                                          "date_to": (start + timedelta(days=6)).isoformat()})
        days = plan.get("days", [])
        signature = json.dumps(days, sort_keys=True) + today.isoformat()
        if signature != self._board_signature:
            self._board_signature = signature
            self._render_board(days, today)
        self.subtitle.configure(text=f"{start.strftime('%d %b')} – {(start + timedelta(days=6)).strftime('%d %b')}")

    def _refresh_availability(self, today: date) -> None:
        data = self.app.read("get_availability")
        if not data:
            return
        signature = json.dumps(data, sort_keys=True) + "".join(map(str, self._order()))
        if signature == self._availability_signature:
            return
        self._availability_signature = signature
        clear(self.grid_holder)
        self.rows.clear()
        by_day = {d["weekday"]: d for d in data["days"]}
        for col, title in enumerate(("Day", "Minutes", "Start", "Peak")):
            theme.label(self.grid_holder, title, 11, color=theme.MUTED).grid(row=0, column=col, sticky="w", padx=6)
            theme.label(self.grid_holder, title, 11, color=theme.MUTED).grid(row=0, column=col + 5, sticky="w", padx=6)
        self.grid_holder.grid_columnconfigure(4, minsize=28)
        for i, wd in enumerate(self._order()):
            d = by_day[wd]
            base = 0 if i < 4 else 5
            row = 1 + (i if i < 4 else i - 4)
            theme.label(self.grid_holder, config.WEEKDAY_LONG[wd], 12, "bold").grid(
                row=row, column=base, sticky="w", padx=6, pady=3)
            spin = Spinbox(self.grid_holder, d["minutes"], step=15)
            spin.grid(row=row, column=base + 1, padx=6, pady=3)
            start_var = ctk.StringVar(value=d["start_time"])
            entry(self.grid_holder, start_var, width=64).grid(row=row, column=base + 2, padx=6, pady=3)
            peak_var = ctk.StringVar(value=d["peak_window"])
            option_menu(self.grid_holder, peak_var, list(config.PEAK_WINDOWS), width=110).grid(
                row=row, column=base + 3, padx=6, pady=3)
            self.rows[wd] = (spin, start_var, peak_var)
        override = next((o for o in data["overrides"] if o["date"] == today.isoformat()), None)
        if override:
            self.override.set(override["minutes"])
            self.override_note.configure(text=f"override active: {override['minutes']} min today")
        else:
            self.override.set(by_day[today.weekday()]["minutes"])
            self.override_note.configure(text="")

    def _save_week(self) -> None:
        days = [{"weekday": wd, "minutes": spin.get(), "start_time": start.get().strip(), "peak_window": peak.get()}
                for wd, (spin, start, peak) in self.rows.items()]
        self._availability_signature = ""
        self.app.call("set_weekly_availability", {"days": days})

    def _apply_override(self) -> None:
        self._availability_signature = ""
        self.app.call("set_day_override", {"date": self.app.today().isoformat(), "minutes": self.override.get()})

    def _clear_override(self) -> None:
        self._availability_signature = ""
        self.app.call("set_day_override", {"date": self.app.today().isoformat(), "clear": True})

    # ------------------------------------------------------------ board
    def _render_board(self, days: list[dict[str, Any]], today: date) -> None:
        clear(self.board)
        for col, day in enumerate(days):
            d = date.fromisoformat(day["date"])
            is_today = d == today
            column = theme.card(self.board, fg_color=theme.SURFACE if is_today else theme.SURFACE_2,
                                border_color=theme.ACCENT if is_today else theme.BORDER)
            column.grid(row=0, column=col, sticky="nsew", padx=3, pady=2)
            theme.label(column, f"{day['weekday'][:3]} {d.day}", 12, "bold",
                        theme.ACCENT if is_today else theme.TEXT).pack(anchor="w", padx=8, pady=(8, 0))
            planned = sum(b["min"] for b in day["blocks"] if b["status"] != "skipped")
            theme.label(column, f"{planned} / {day['capacity_min']} min", 10, color=theme.MUTED).pack(
                anchor="w", padx=8, pady=(0, 4))
            if not day["blocks"]:
                theme.label(column, "—", 11, color=theme.MUTED).pack(anchor="w", padx=8, pady=(0, 8))
            for b in day["blocks"]:
                chip = ctk.CTkFrame(column, fg_color=b["color"], corner_radius=6)
                chip.pack(fill="x", padx=6, pady=2)
                text = f"{_MARK.get(b['status'], '')}{b['start']} {_short(b['subject'])}\n{_ABBR.get(b['type'], '')} · u{b['unit']}"
                ctk.CTkLabel(chip, text=text, font=theme.font(10, "bold"), justify="left", anchor="w",
                             text_color="#10131A" if b["status"] != "skipped" else "#4A4F5C").pack(
                    anchor="w", padx=6, pady=3)
            ctk.CTkFrame(column, fg_color="transparent", height=6).pack()


def _short(name: str) -> str:
    return name if len(name) <= 14 else "".join(w[0] for w in name.split()).upper() if " " in name else name[:13] + "…"
