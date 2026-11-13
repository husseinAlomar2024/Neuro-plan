"""Week view (Qt)."""
from __future__ import annotations

from . import BaseView


class WeekView(BaseView):
    title = "Week"
    loaded = False

    def refresh(self) -> None:
        return None

    def tick(self) -> None:
        return None
