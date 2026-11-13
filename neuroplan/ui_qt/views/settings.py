"""Settings view (Qt)."""
from __future__ import annotations

from . import BaseView


class SettingsView(BaseView):
    title = "Settings"
    loaded = False

    def refresh(self) -> None:
        return None

    def tick(self) -> None:
        return None
