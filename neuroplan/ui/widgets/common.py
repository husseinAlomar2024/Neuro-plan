"""Small shared widgets: KPI card, minute spinbox, colour chip, labelled form field."""
from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from .. import theme


class KpiCard(ctk.CTkFrame):
    def __init__(self, master: object, title: str) -> None:
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=12, border_width=1,
                         border_color=theme.BORDER)
        self._title = theme.label(self, title.upper(), 10, "bold", theme.MUTED)
        self._title.pack(anchor="w", padx=14, pady=(10, 0))
        self._value = theme.label(self, "–", 24, "bold")
        self._value.pack(anchor="w", padx=14)
        self._hint = theme.label(self, "", 11, color=theme.MUTED)
        self._hint.pack(anchor="w", padx=14, pady=(0, 10))

    def set(self, value: str, hint: str = "", color: Optional[theme.Color] = None) -> None:
        self._value.configure(text=value, text_color=color or theme.TEXT)
        self._hint.configure(text=hint)


class Spinbox(ctk.CTkFrame):
    """Integer entry with − / + buttons."""

    def __init__(self, master: object, value: int = 0, step: int = 15, minimum: int = 0, maximum: int = 960,
                 width: int = 118, command: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.step, self.minimum, self.maximum, self.command = step, minimum, maximum, command
        self.var = ctk.StringVar(value=str(value))
        ctk.CTkButton(self, text="−", width=26, height=28, corner_radius=6, fg_color=theme.SURFACE_2,
                      hover_color=theme.SURFACE_3, text_color=theme.TEXT, command=lambda: self._bump(-1)
                      ).pack(side="left")
        self.entry = ctk.CTkEntry(self, textvariable=self.var, width=max(40, width - 60), height=28,
                                  justify="center", border_color=theme.BORDER, fg_color=theme.SURFACE_2,
                                  text_color=theme.TEXT)
        self.entry.pack(side="left", padx=3)
        ctk.CTkButton(self, text="+", width=26, height=28, corner_radius=6, fg_color=theme.SURFACE_2,
                      hover_color=theme.SURFACE_3, text_color=theme.TEXT, command=lambda: self._bump(1)
                      ).pack(side="left")

    def _bump(self, direction: int) -> None:
        self.set(self.get() + direction * self.step)
        if self.command:
            self.command()

    def get(self) -> int:
        try:
            value = int(float(self.var.get().strip() or 0))
        except ValueError:
            value = self.minimum
        return max(self.minimum, min(self.maximum, value))

    def set(self, value: int) -> None:
        self.var.set(str(max(self.minimum, min(self.maximum, int(value)))))


class Chip(ctk.CTkFrame):
    """A small coloured dot followed by text."""

    def __init__(self, master: object, text: str, color: str, size: int = 12, weight: str = "bold") -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkFrame(self, width=10, height=10, corner_radius=5, fg_color=color, border_width=1,
                     border_color=theme.BORDER).pack(side="left", padx=(0, 6))
        self.label = theme.label(self, text, size, weight)
        self.label.pack(side="left")


def field(master: object, title: str, widget_factory: Callable[[ctk.CTkFrame], ctk.CTkBaseClass],
          row: int, column: int = 0, columnspan: int = 1) -> ctk.CTkBaseClass:
    """Place a titled form field in a grid cell and return the inner widget."""
    holder = ctk.CTkFrame(master, fg_color="transparent")
    holder.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=6, pady=4)
    theme.label(holder, title, 11, color=theme.MUTED).pack(anchor="w")
    widget = widget_factory(holder)
    widget.pack(anchor="w", fill="x")
    return widget


def entry(master: object, variable: ctk.StringVar, width: int = 140, show: str = "",
          placeholder: str = "") -> ctk.CTkEntry:
    kwargs: dict[str, object] = {"textvariable": variable, "width": width, "height": 30,
                                 "border_color": theme.BORDER, "fg_color": theme.SURFACE_2,
                                 "text_color": theme.TEXT}
    if show:
        kwargs["show"] = show
    if placeholder:
        kwargs["placeholder_text"] = placeholder
    return ctk.CTkEntry(master, **kwargs)  # type: ignore[arg-type]


def option_menu(master: object, variable: ctk.StringVar, values: list[str], width: int = 140,
                command: Optional[Callable[[str], None]] = None) -> ctk.CTkOptionMenu:
    menu = ctk.CTkOptionMenu(master, variable=variable, values=values, width=width, height=30,
                             fg_color=theme.SURFACE_2, button_color=theme.SURFACE_3,
                             button_hover_color=theme.BORDER, text_color=theme.TEXT,
                             dropdown_fg_color=theme.SURFACE, dropdown_text_color=theme.TEXT,
                             dropdown_hover_color=theme.SURFACE_3, font=theme.font(12), command=command)
    return theme.no_nested_idle_flush(menu)


def clear(frame: ctk.CTkBaseClass) -> None:
    for child in frame.winfo_children():
        child.destroy()
