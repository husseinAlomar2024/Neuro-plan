"""Simple in-process publish/subscribe bus. Handlers run synchronously on the emitting thread."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

# Event names used across the app
STATE_CHANGED = "state_changed"
PLAN_CHANGED = "plan_changed"
BLOCK_FINISHED = "block_finished"
BLOCK_SKIPPED = "block_skipped"
MISSED_YESTERDAY = "missed_yesterday"
RISK_DETECTED = "risk_detected"
ONBOARDING_NEEDED = "onboarding_needed"
DAILY_BRIEFING = "daily_briefing"
END_OF_DAY = "end_of_day"
REVIEWS_DUE = "reviews_due"
BREAK_STARTED = "break_started"
ERROR = "error"
CHAT_MESSAGE = "chat_message"     # a message was added to the chat (payload: message)
COACH_BUSY = "coach_busy"         # payload: busy (bool)


@dataclass
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._lock = threading.Lock()

    def subscribe(self, name: str, handler: Handler) -> None:
        """Subscribe to one event name, or to every event with name '*'."""
        with self._lock:
            self._handlers.setdefault(name, []).append(handler)

    def unsubscribe(self, name: str, handler: Handler) -> None:
        with self._lock:
            if handler in self._handlers.get(name, []):
                self._handlers[name].remove(handler)

    def emit(self, name: str, **payload: Any) -> None:
        event = Event(name, payload)
        with self._lock:
            handlers = list(self._handlers.get(name, [])) + list(self._handlers.get("*", []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # a broken listener must never break the emitter
                log.exception("event handler failed for %s", name)
