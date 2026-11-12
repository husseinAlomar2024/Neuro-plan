"""Centre views: Today, Week, Subjects, Reviews, Settings."""
from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from .. import theme

if TYPE_CHECKING:
    from ..app import NeuroPlanApp


class BaseView(ctk.CTkFrame):
    """A centre view. ``refresh`` re-reads everything through the app's tool calls."""
    title = ""

    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, fg_color=theme.BG, corner_radius=0)
        self.app = app

    def refresh(self) -> None:
        raise NotImplementedError

    def theme_changed(self) -> None:
        """Called after a live theme switch. Widgets redraw themselves; views with cached colours override."""

    def header(self, subtitle: str = "") -> ctk.CTkFrame:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=theme.PAD + 4, pady=(theme.PAD + 2, 6))
        theme.label(bar, self.title, 22, "bold").pack(side="left")
        self.subtitle = theme.label(bar, subtitle, 12, color=theme.MUTED)
        self.subtitle.pack(side="left", padx=12, pady=(8, 0))
        return bar
