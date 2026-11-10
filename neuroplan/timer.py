"""Pomodoro state machine. No UI code: the UI calls tick() once per second and renders the state.

Nothing is ever logged automatically. When the focus time ends the timer only raises
``awaiting_confirmation``; the student's answer is turned into a tool call by confirm_*()."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

IDLE = "idle"
FOCUS = "focus"
BREAK = "break"


class TimerError(Exception):
    pass


@dataclass
class TimerCall:
    """The tool call that results from the student's answer."""
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


class PomodoroTimer:
    def __init__(self, on_focus_end: Optional[Callable[[], None]] = None,
                 on_break_start: Optional[Callable[[], None]] = None,
                 on_break_end: Optional[Callable[[], None]] = None) -> None:
        self.on_focus_end = on_focus_end
        self.on_break_start = on_break_start
        self.on_break_end = on_break_end
        self.phase = IDLE
        self.paused = False
        self.block_id: Optional[int] = None
        self.label = ""
        self.focus_min = 0
        self.break_min = 0
        self.remaining_seconds = 0
        self.elapsed_focus_seconds = 0
        self.awaiting_confirmation = False
        self._ended_early = False

    # ------------------------------------------------------------ control
    def start(self, block_id: int, focus_min: int, break_min: int, label: str = "") -> None:
        """Start the focus phase for a block (replaces whatever was running)."""
        if focus_min <= 0:
            raise TimerError("focus time must be positive")
        self.phase = FOCUS
        self.paused = False
        self.block_id = block_id
        self.label = label
        self.focus_min = focus_min
        self.break_min = max(0, break_min)
        self.remaining_seconds = focus_min * 60
        self.elapsed_focus_seconds = 0
        self.awaiting_confirmation = False
        self._ended_early = False

    def pause(self) -> None:
        if self.phase != IDLE:
            self.paused = True

    def resume(self) -> None:
        self.paused = False

    def toggle_pause(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def reset(self) -> None:
        """Stop everything and forget the block. Nothing is logged."""
        self.phase = IDLE
        self.paused = False
        self.block_id = None
        self.label = ""
        self.remaining_seconds = 0
        self.elapsed_focus_seconds = 0
        self.awaiting_confirmation = False
        self._ended_early = False

    def finish_early(self) -> None:
        """The student stops the focus phase before the time is up; still needs confirmation."""
        if self.phase != FOCUS:
            raise TimerError("no focus phase is running")
        self._ended_early = True
        self._end_focus()

    def tick(self, seconds: int = 1) -> None:
        """Advance the clock. Focus → break switches automatically."""
        if self.phase == IDLE or self.paused or seconds <= 0:
            return
        left = seconds
        while left > 0 and self.phase != IDLE:
            step = min(left, self.remaining_seconds)
            self.remaining_seconds -= step
            left -= step
            if self.phase == FOCUS:
                self.elapsed_focus_seconds += step
            if self.remaining_seconds > 0:
                break
            if self.phase == FOCUS:
                self._end_focus()
            else:
                self._end_break()

    def _end_focus(self) -> None:
        self.awaiting_confirmation = True
        if self.on_focus_end:
            self.on_focus_end()
        if self.break_min > 0:
            self.phase = BREAK
            self.paused = False
            self.remaining_seconds = self.break_min * 60
            if self.on_break_start:
                self.on_break_start()
        else:
            self.phase = IDLE
            self.remaining_seconds = 0

    def _end_break(self) -> None:
        self.phase = IDLE
        self.remaining_seconds = 0
        if self.on_break_end:
            self.on_break_end()

    # ------------------------------------------------------------ state for the UI
    @property
    def running(self) -> bool:
        return self.phase != IDLE and not self.paused

    @property
    def elapsed_focus_minutes(self) -> int:
        """Whole minutes of focus so far (at least 1 once anything was studied)."""
        if self.elapsed_focus_seconds <= 0:
            return 0
        return max(1, round(self.elapsed_focus_seconds / 60))

    def clock(self) -> str:
        return f"{self.remaining_seconds // 60:02d}:{self.remaining_seconds % 60:02d}"

    # ------------------------------------------------------------ confirmation → tool calls
    def _answer(self) -> int:
        if not self.awaiting_confirmation or self.block_id is None:
            raise TimerError("there is nothing to confirm")
        block_id = self.block_id
        self.awaiting_confirmation = False
        self.block_id = None
        return block_id

    def confirm_done(self) -> TimerCall:
        """Done → complete_block with the minutes studied."""
        minutes = self.elapsed_focus_minutes if self._ended_early else self.focus_min
        block_id = self._answer()
        return TimerCall("complete_block", {"id": block_id, "actual_min": minutes, "status": "done"})

    def confirm_partial(self, minutes: Optional[int] = None) -> TimerCall:
        """Partial → complete_block(status=partial) with the minutes actually studied
        (defaults to the elapsed focus minutes, which the UI pre-fills)."""
        actual = self.elapsed_focus_minutes if minutes is None else minutes
        if actual < 0:
            raise TimerError("minutes must not be negative")
        block_id = self._answer()
        return TimerCall("complete_block", {"id": block_id, "actual_min": int(actual), "status": "partial"})

    def confirm_not_studied(self) -> TimerCall:
        """Didn't really study → skip_block."""
        block_id = self._answer()
        return TimerCall("skip_block", {"id": block_id, "reason": "Didn't really study"})
