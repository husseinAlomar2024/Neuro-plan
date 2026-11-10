"""The always-visible coach panel: chat bubbles, action badges with Undo, confirm cards, option buttons,
quick chips, typing indicator. Collapsible to a strip with an unread badge."""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Optional

import customtkinter as ctk

from .. import config
from ..models import ChatMessage
from . import theme

if TYPE_CHECKING:
    from .app import NeuroPlanApp

_WRAP = theme.COACH_WIDTH - 86
_MAX_RENDERED = 80


class CoachPanel(ctk.CTkFrame):
    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=0, width=theme.COACH_WIDTH)
        self.app = app
        self.collapsed = False
        self.unread = 0
        self._interactive: list[ctk.CTkButton] = []       # buttons of the latest coach message
        self._undo_button: Optional[ctk.CTkButton] = None
        self._rendered = 0
        self.grid_propagate(False)
        self.pack_propagate(False)

        # ---- collapsed strip
        self.strip = ctk.CTkFrame(self, fg_color="transparent")
        self.strip_button = ctk.CTkButton(self.strip, text="💬", width=40, height=40, corner_radius=10,
                                          fg_color=theme.SURFACE_2, hover_color=theme.SURFACE_3,
                                          text_color=theme.TEXT, font=theme.font(18), command=self.toggle)
        self.strip_button.pack(pady=(14, 4))
        self.badge = ctk.CTkLabel(self.strip, text="", width=24, height=20, corner_radius=10,
                                  fg_color=theme.DANGER, text_color="#FFFFFF", font=theme.font(11, "bold"))

        # ---- expanded panel
        self.panel = ctk.CTkFrame(self, fg_color="transparent")
        self.panel.pack(fill="both", expand=True)
        head = ctk.CTkFrame(self.panel, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(14, 6))
        theme.label(head, "Coach", 18, "bold").pack(side="left")
        self.status = theme.label(head, "", 11, color=theme.MUTED)
        self.status.pack(side="left", padx=10, pady=(4, 0))
        ctk.CTkButton(head, text="⟩", width=30, height=28, corner_radius=8, fg_color=theme.SURFACE_2,
                      hover_color=theme.SURFACE_3, text_color=theme.TEXT, command=self.toggle).pack(side="right")

        self.messages = ctk.CTkScrollableFrame(self.panel, fg_color=theme.BG, corner_radius=10)
        self.messages.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.typing = theme.label(self.panel, "", 11, color=theme.MUTED)
        self.typing.pack(anchor="w", padx=16)

        chips = ctk.CTkFrame(self.panel, fg_color="transparent")
        chips.pack(fill="x", padx=10, pady=(2, 4))
        for i, text in enumerate(config.QUICK_CHIPS):
            ctk.CTkButton(chips, text=text, height=26, width=10, corner_radius=13, fg_color=theme.SURFACE_2,
                          hover_color=theme.SURFACE_3, text_color=theme.TEXT, border_width=1,
                          border_color=theme.BORDER, font=theme.font(11),
                          command=lambda t=text: self._chip(t)).grid(row=i // 3, column=i % 3, padx=3, pady=3,
                                                                     sticky="w")
        bottom = ctk.CTkFrame(self.panel, fg_color="transparent")
        bottom.pack(fill="x", padx=10, pady=(0, 12))
        self.input = ctk.CTkTextbox(bottom, height=64, corner_radius=10, fg_color=theme.SURFACE_2,
                                    border_width=1, border_color=theme.BORDER, text_color=theme.TEXT,
                                    font=theme.font(13), wrap="word")
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Return>", self._on_return)
        self.send_button = theme.primary_button(bottom, "Send", self.send, width=58, height=64)
        self.send_button.pack(side="left", padx=(6, 0))
        self.set_status()

    # ------------------------------------------------------------ collapse / unread
    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.panel.pack_forget()
            self.strip.pack(fill="both", expand=True)
            self.configure(width=64)
        else:
            self.strip.pack_forget()
            self.panel.pack(fill="both", expand=True)
            self.configure(width=theme.COACH_WIDTH)
            self.unread = 0
            self._scroll_down()
        self._show_badge()

    def expand(self) -> None:
        if self.collapsed:
            self.toggle()

    def _show_badge(self) -> None:
        if self.collapsed and self.unread:
            self.badge.configure(text=str(self.unread))
            self.badge.pack()
        else:
            self.badge.pack_forget()

    def set_status(self) -> None:
        self.status.configure(text=self.app.coach.status_text())

    def set_busy(self, busy: bool) -> None:
        self.typing.configure(text="Coach is typing…" if busy else "")
        self.send_button.configure(state="disabled" if busy else "normal")

    # ------------------------------------------------------------ input
    def _on_return(self, event: Any) -> Optional[str]:
        if event.state & 0x0001:          # Shift+Enter inserts a newline
            return None
        self.send()
        return "break"

    def send(self) -> None:
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self.app.coach.submit(text)

    def _chip(self, text: str) -> None:
        if text.endswith("…"):            # "I'm behind in…" needs the student to finish the sentence
            self.input.delete("1.0", "end")
            self.input.insert("1.0", "I'm 2 lectures behind in ")
            self.input.focus_set()
            return
        self.app.coach.submit(text)

    # ------------------------------------------------------------ messages
    def load_history(self) -> None:
        for child in self.messages.winfo_children():
            child.destroy()
        self._interactive.clear()
        self._undo_button = None
        self._rendered = 0
        for message in self.app.coach.history(_MAX_RENDERED):
            self.add_message(message, live=False)
        self._scroll_down()

    def add_message(self, message: ChatMessage, live: bool = True) -> None:
        if message.role != "user":
            self._retire_interactive()
        if message.role == "user":
            self._bubble(message.text, theme.USER_BUBBLE, "e")
        elif message.role == "system_event":
            theme.label(self.messages, message.text, 11, color=theme.MUTED, wraplength=_WRAP + 30,
                        justify="center").pack(fill="x", padx=10, pady=4)
        else:
            self._coach_message(message)
        self._rendered += 1
        if live:
            if self.collapsed and message.role != "user":
                self.unread += 1
                self._show_badge()
            self._scroll_down()

    def _bubble(self, text: str, color: theme.Color, anchor: str, tag: str = "") -> ctk.CTkFrame:
        holder = ctk.CTkFrame(self.messages, fg_color="transparent")
        holder.pack(fill="x", padx=6, pady=3)
        bubble = ctk.CTkFrame(holder, fg_color=color, corner_radius=12)
        bubble.pack(anchor=anchor, padx=(28, 0) if anchor == "e" else (0, 28))
        if tag:
            theme.label(bubble, tag, 9, "bold", theme.ACCENT).pack(anchor="w", padx=10, pady=(6, 0))
        theme.label(bubble, text, 12, wraplength=_WRAP, justify="left", anchor="w").pack(
            anchor="w", padx=10, pady=(2 if tag else 7, 7))
        return bubble

    def _coach_message(self, message: ChatMessage) -> None:
        bubble = self._bubble(message.text, theme.COACH_BUBBLE, "w", "COACH" if message.proactive else "")
        payload = message.actions or {}
        actions = payload.get("actions") or []
        if actions:
            box = ctk.CTkFrame(bubble, fg_color="transparent")
            box.pack(fill="x", padx=8, pady=(0, 6))
            theme.label(box, "ACTIONS EXECUTED", 9, "bold", theme.MUTED).pack(anchor="w", padx=2)
            for action in actions:
                ok = action.get("ok")
                theme.label(box, ("✓ " if ok else "✕ ") + str(action.get("summary", "")), 11,
                            color=theme.SUCCESS if ok else theme.DANGER, wraplength=_WRAP - 10, justify="left",
                            anchor="w").pack(anchor="w", padx=2)
            if any(a.get("ok") and a.get("action_id") and a.get("tool") != "undo_last" for a in actions):
                if self._undo_button is not None and self._undo_button.winfo_exists():
                    self._undo_button.destroy()          # Undo only on the latest action
                self._undo_button = ctk.CTkButton(
                    box, text="Undo", width=44, height=20, fg_color="transparent", hover_color=theme.SURFACE_3,
                    text_color=theme.ACCENT, font=theme.font(11, "bold"), command=self._undo)
                self._undo_button.pack(anchor="w")
        for card in payload.get("confirmations") or []:
            box = ctk.CTkFrame(bubble, fg_color=theme.SURFACE_3, corner_radius=8)
            box.pack(fill="x", padx=8, pady=(0, 8))
            theme.label(box, card.get("preview", "Confirm?"), 12, "bold", wraplength=_WRAP - 24, justify="left"
                        ).pack(anchor="w", padx=8, pady=(8, 4))
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(anchor="w", padx=6, pady=(0, 8))
            confirm = theme.primary_button(row, "Confirm", width=76, height=26)
            cancel = theme.ghost_button(row, "Cancel", width=66, height=26)
            confirm.configure(command=lambda c=card: self._option({"type": "confirm", **c}, "Confirm"))
            cancel.configure(command=lambda: self._option({"type": "cancel"}, "Cancel"))
            confirm.pack(side="left", padx=2)
            cancel.pack(side="left", padx=2)
            self._interactive += [confirm, cancel]
        options = payload.get("options") or []
        if options:
            box = ctk.CTkFrame(bubble, fg_color="transparent")
            box.pack(fill="x", padx=8, pady=(0, 8))
            for option in options:
                button = ctk.CTkButton(box, text=option["label"], height=26, corner_radius=13,
                                       fg_color=theme.SURFACE, hover_color=theme.SURFACE_3, border_width=1,
                                       border_color=theme.ACCENT, text_color=theme.ACCENT, font=theme.font(11, "bold"),
                                       command=lambda o=option: self._option(o["payload"], o["label"]))
                button.pack(anchor="w", pady=2)
                self._interactive.append(button)

    def _retire_interactive(self) -> None:
        """Buttons of older coach messages are disabled so stale options cannot be clicked."""
        for button in self._interactive:
            if button.winfo_exists():
                button.configure(state="disabled")
        self._interactive.clear()

    def _option(self, payload: dict[str, Any], label: str) -> None:
        self._retire_interactive()
        self.app.coach.submit_option(payload, label)

    def _undo(self) -> None:
        if self._undo_button is not None and self._undo_button.winfo_exists():
            self._undo_button.destroy()
        self._undo_button = None
        self.app.coach.submit_option({"type": "undo"}, "Undo")

    def _scroll_down(self) -> None:
        def move() -> None:
            with contextlib.suppress(Exception):       # the panel may already be destroyed
                self.messages._parent_canvas.yview_moveto(1.0)  # noqa: SLF001 - no public API for this
        self.after(60, move)
