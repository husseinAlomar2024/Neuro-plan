"""Subjects view (Qt)."""
from __future__ import annotations

from . import BaseView


class SubjectsView(BaseView):
    title = "Subjects"
    loaded = False

    def refresh(self) -> None:
        return None

    def tick(self) -> None:
        return None
