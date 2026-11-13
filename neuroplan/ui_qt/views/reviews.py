"""Reviews view (Qt)."""
from __future__ import annotations

from . import BaseView


class ReviewsView(BaseView):
    title = "Reviews"
    loaded = False

    def refresh(self) -> None:
        return None

    def tick(self) -> None:
        return None
