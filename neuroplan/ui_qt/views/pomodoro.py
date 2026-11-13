"""Pomodoro card: renders the PomodoroTimer state machine and asks for confirmation when focus ends.
Nothing is logged before the student answers Done / Partial / Didn't really study."""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from ... import timer as timer_mod
from ...timer import PomodoroTimer, TimerCall, TimerError
from ..style import set_prop
from ..widgets import Card, button, card, label, spinbox


class PomodoroWidget(Card):
    def __init__(self, timer: PomodoroTimer, on_call: Callable[[TimerCall], None]) -> None:
        super().__init__("true")
        self.timer = timer
        self.on_call = on_call
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(16)
        self.clock = label("00:00", "clock")
        top.addWidget(self.clock)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.phase = label("POMODORO", "caption")
        info.addWidget(self.phase)
        self.title = label("Press Start on a block to begin.", wrap=True)
        info.addWidget(self.title)
        top.addLayout(info, 1)
        self.pause_button = button("Pause", self._pause, tooltip="Pause or resume the timer")
        self.finish_button = button("Finish", self._finish, tooltip="End the focus phase now")
        self.reset_button = button("Reset", self._reset, tooltip="Stop the timer without logging anything")
        for widget in (self.pause_button, self.finish_button, self.reset_button):
            top.addWidget(widget)
        outer.addLayout(top)

        # confirmation card, shown only when the focus time is over
        self.confirm, confirm_layout = card("inset", margins=(12, 10, 12, 10))
        self.confirm_text = label("", "strong", wrap=True)
        confirm_layout.addWidget(self.confirm_text)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.done_button = button("Done", self._done, kind="primary", tooltip="Log the block as done")
        row.addWidget(self.done_button)
        row.addSpacing(8)
        self.partial_minutes = spinbox(0, step=5, minimum=0, maximum=600)
        self.partial_minutes.setToolTip("Minutes actually studied")
        row.addWidget(self.partial_minutes)
        row.addWidget(label("min", "muted"))
        self.partial_button = button("Partial", self._partial, tooltip="Log only the minutes above")
        row.addWidget(self.partial_button)
        row.addSpacing(8)
        self.not_studied_button = button("Didn't really study", self._not_studied, tone="danger",
                                         tooltip="Skip the block; nothing is logged as studied")
        row.addWidget(self.not_studied_button)
        row.addStretch(1)
        confirm_layout.addLayout(row)
        outer.addWidget(self.confirm)
        self.confirm.hide()
        self._confirm_visible = False
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
        minutes = self.partial_minutes.value()
        self._answer(lambda: self.timer.confirm_partial(minutes))

    def _not_studied(self) -> None:
        self._answer(self.timer.confirm_not_studied)

    # ------------------------------------------------------------ rendering
    def refresh(self) -> None:
        t = self.timer
        self.clock.setText(t.clock())
        if t.phase == timer_mod.FOCUS:
            self.phase.setText("FOCUS · PAUSED" if t.paused else "FOCUS")
            set_prop(self.phase, "tone", "accent")
            self.title.setText(t.label or "Focus")
        elif t.phase == timer_mod.BREAK:
            self.phase.setText("BREAK · PAUSED" if t.paused else "BREAK")
            set_prop(self.phase, "tone", "success")
            self.title.setText("Step away for a moment.")
        else:
            self.phase.setText("POMODORO")
            set_prop(self.phase, "tone", "")
            self.title.setText("Waiting for your answer below." if t.awaiting_confirmation
                               else "Press Start on a block to begin.")
        active = t.phase != timer_mod.IDLE
        self.pause_button.setText("Resume" if t.paused else "Pause")
        self.pause_button.setEnabled(active)
        self.finish_button.setEnabled(t.phase == timer_mod.FOCUS)
        self.reset_button.setEnabled(active or t.awaiting_confirmation)
        if t.awaiting_confirmation and not self._confirm_visible:
            self.partial_minutes.setValue(t.elapsed_focus_minutes)
            self.confirm_text.setText(f"Focus time is over — {t.label or 'this block'}. Did you study?")
            self.confirm.show()
            self._confirm_visible = True
            self.done_button.setFocus()
        elif not t.awaiting_confirmation and self._confirm_visible:
            self.confirm.hide()
            self._confirm_visible = False
