"""Today view (Qt)."""
from __future__ import annotations

from . import BaseView


class TodayView(BaseView):
    title = "Today"
    loaded = False

    def refresh(self) -> None:
        return None

    def tick(self) -> None:
        return None
