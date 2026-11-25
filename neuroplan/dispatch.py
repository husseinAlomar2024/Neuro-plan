"""Running work on the UI thread, without knowing which UI toolkit is used.

Worker threads (the coach's LLM calls, a connection test) must not touch widgets. They hand a callable
to a ``Dispatcher``; the UI implementation runs it on its own thread (Qt: a queued signal). Code outside
the UI only ever sees this protocol."""
from __future__ import annotations

from typing import Callable, Protocol


class Dispatcher(Protocol):
    def post(self, fn: Callable[[], None]) -> None:
        """Thread-safe: run ``fn`` on the UI thread as soon as possible."""


class ImmediateDispatcher:
    """Runs callables at once on the calling thread (tests and scripts without a UI)."""

    def post(self, fn: Callable[[], None]) -> None:
        fn()
