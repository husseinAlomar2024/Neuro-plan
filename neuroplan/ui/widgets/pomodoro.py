"""Pomodoro card: renders the PomodoroTimer state machine and asks for confirmation when focus ends."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from ... import timer as timer_mod
from ...timer import PomodoroTimer, TimerCall, TimerError
from .. import theme
from .common import entry


class PomodoroWidget(ctk.CTkFrame):
    def __init__(self, master: object, timer: PomodoroTimer, on_call: Callable[[TimerCall], None]) -> None:
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=12, border_width=1,
                         border_color=theme.BORDER)
        self.timer = timer
        self.on_call = on_call
        self._confirm_visible = False

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 10))
        self.clock = ctk.CTkLabel(top, text="00:00", font=theme.mono(34, "bold"), text_color=theme.TEXT)
        self.clock.pack(side="left")
        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="left", padx=16, fill="x", expand=True)
        self.phase = theme.label(info, "POMODORO", 10, "bold", theme.MUTED)
        self.phase.pack(anchor="w")
        self.title = theme.label(info, "Press Start on a block to begin.", 13)
        self.title.pack(anchor="w")
        self.reset_button = theme.ghost_button(top, "Reset", self._reset, width=70)
        self.reset_button.pack(side="right", padx=(6, 0))
        self.finish_button = theme.ghost_button(top, "Finish", self._finish, width=70)
        self.finish_button.pack(side="right", padx=(6, 0))
        self.pause_button = theme.ghost_button(top, "Pause", self._pause, width=80)
        self.pause_button.pack(side="right")

        # confirmation card: shown only when the focus time is over. Nothing is logged before an answer.
        self.confirm = ctk.CTkFrame(self, fg_color=theme.SURFACE_2, corner_radius=10)
        self.confirm_text = theme.label(self.confirm, "", 13, "bold")
        self.confirm_text.pack(anchor="w", padx=12, pady=(10, 6))
        row = ctk.CTkFrame(self.confirm, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))
        theme.primary_button(row, "Done", self._done, width=80).pack(side="left")
        self.partial_var = ctk.StringVar(value="0")
        entry(row, self.partial_var, width=56).pack(side="left", padx=(14, 4))
        theme.label(row, "min", 12, color=theme.MUTED).pack(side="left")
        theme.ghost_button(row, "Partial", self._partial, width=76).pack(side="left", padx=(6, 14))
        theme.ghost_button(row, "Didn't really study", self._not_studied, width=140,
                           text_color=theme.DANGER).pack(side="left")
        self.refresh()

    # ------------------------------------------------------------ actions
    def _pause(self) -> None:
        self.timer.toggle_pause()
        self.refresh()

    def _reset(self) -> None:
        self.timer.reset()
        self.refresh()

    def _finish(self) -> None:
        try:
            self.timer.finish_early()
        except TimerError:
            return
        self.refresh()

    def _answer(self, make: Callable[[], TimerCall]) -> None:
        try:
            call = make()
        except TimerError:
            self.refresh()
            return
        self.refresh()
        self.on_call(call)

    def _done(self) -> None:
        self._answer(self.timer.confirm_done)

    def _partial(self) -> None:
        try:
            minutes = max(0, int(float(self.partial_var.get().strip() or 0)))
        except ValueError:
            minutes = self.timer.elapsed_focus_minutes
        self._answer(lambda: self.timer.confirm_partial(minutes))

    def _not_studied(self) -> None:
        self._answer(self.timer.confirm_not_studied)

    # ------------------------------------------------------------ rendering
    def refresh(self) -> None:
        t = self.timer
        self.clock.configure(text=t.clock())
        if t.phase == timer_mod.FOCUS:
            self.phase.configure(text="FOCUS · PAUSED" if t.paused else "FOCUS", text_color=theme.ACCENT_TEXT)
            self.title.configure(text=t.label or "Focus")
        elif t.phase == timer_mod.BREAK:
            self.phase.configure(text="BREAK · PAUSED" if t.paused else "BREAK", text_color=theme.SUCCESS)
            self.title.configure(text="Step away for a moment.")
        else:
            self.phase.configure(text="POMODORO", text_color=theme.MUTED)
            self.title.configure(text="Waiting for your answer below." if t.awaiting_confirmation
                                 else "Press Start on a block to begin.")
        active = t.phase != timer_mod.IDLE
        self.pause_button.configure(text="Resume" if t.paused else "Pause",
                                    state="normal" if active else "disabled")
        self.finish_button.configure(state="normal" if t.phase == timer_mod.FOCUS else "disabled")
        self.reset_button.configure(state="normal" if active or t.awaiting_confirmation else "disabled")
        if t.awaiting_confirmation and not self._confirm_visible:
            self.partial_var.set(str(t.elapsed_focus_minutes))
            self.confirm_text.configure(text=f"Focus time is over — {t.label or 'this block'}. Did you study?")
            self.confirm.pack(fill="x", padx=16, pady=(0, 12))
            self._confirm_visible = True
        elif not t.awaiting_confirmation and self._confirm_visible:
            self.confirm.pack_forget()
            self._confirm_visible = False
