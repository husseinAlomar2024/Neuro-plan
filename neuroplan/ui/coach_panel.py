"""The always-visible coach panel: chat bubbles, action badges with Undo, confirm cards, option buttons,
quick chips, typing indicator. Collapsible to a strip with an unread badge."""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Optional

import customtkinter as ctk

from .. import config
from ..models import ChatMessage
from . import fonts, theme
from .widgets.chat_text import ChatText
from .widgets.debounce import Debounce

if TYPE_CHECKING:
    from .app import NeuroPlanApp

_AREA_GUESS = config.COACH_DEFAULT_WIDTH - 60     # width of the message list until its first <Configure> arrives
_MIN_WRAP = 80
# Horizontal space taken around a text, subtracted from the width of the message list to get its wraplength.
_INSET_BUBBLE = 70       # holder 6+6, bubble side margin 28, text padding 10+10, safety 10
_INSET_BADGE = 72        # same bubble, action box 8+8, line padding 2+2
_INSET_CARD = 84         # same bubble, confirm card 8+8, text padding 8+8
_INSET_EVENT = 28        # centred system line: padding 10+10, safety 8


class CoachPanel(ctk.CTkFrame):
    def __init__(self, master: object, app: "NeuroPlanApp", width: int = config.COACH_DEFAULT_WIDTH) -> None:
        width = max(config.COACH_MIN_WIDTH, int(width))
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=0, width=width)
        self.app = app
        self.width = width                                 # preferred width (the divider position)
        self._shown_width = width
        self.collapsed = False
        self.unread = 0
        self._interactive: list[ctk.CTkButton] = []       # buttons of the latest coach message
        self._undo_button: Optional[ctk.CTkButton] = None
        self._rendered = 0
        self._oldest_id = 0                                # id of the oldest rendered message
        self.has_older = False                             # older messages exist that are not rendered
        self._prepending = False
        self._scroll_pending = False                       # a scroll to the bottom is scheduled
        self._insert_before: Optional[Any] = None
        self._area = _AREA_GUESS                           # current width of the message list (unscaled px)
        self._wrapped: list[tuple[ctk.CTkLabel, int]] = []  # every wrapping text with its inset
        self.grid_propagate(False)
        self.pack_propagate(False)

        # ---- collapsed strip
        self.strip = ctk.CTkFrame(self, fg_color="transparent")
        self.strip_button = ctk.CTkButton(self.strip, text="💬", width=40, height=40, corner_radius=10,
                                          fg_color=theme.SURFACE_2, hover_color=theme.SURFACE_3,
                                          text_color=theme.TEXT, font=theme.font(18), command=self.toggle)
        self.strip_button.pack(pady=(14, 4))
        self.badge = ctk.CTkLabel(self.strip, text="", width=24, height=20, corner_radius=10,
                                  fg_color=theme.DANGER, text_color=theme.ON_DANGER, font=theme.font(11, "bold"))

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

        self.messages = theme.scrollable(self.panel, fg_color=theme.BG, corner_radius=10)
        self.messages.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        # the canvas is exactly as wide as the room the bubbles have (the scrollbar sits outside it)
        # bound once; the texts are re-wrapped once after the last <Configure> of a resize, not per event
        self._rewrap_later = Debounce(self, self._rewrap_keep_scroll)
        self.messages._parent_canvas.bind("<Configure>", self._on_messages_resize, add="+")  # noqa: SLF001
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
                                    font=fonts.chat(), wrap="word", scrollbar_button_color=theme.SCROLLBAR,
                                    scrollbar_button_hover_color=theme.SCROLLBAR_HOVER)
        theme.no_nested_idle_flush(self.input._y_scrollbar)  # noqa: SLF001
        theme.no_nested_idle_flush(self.input._x_scrollbar)  # noqa: SLF001
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
            self.app.coach_divider.grid_remove()
        else:
            self.strip.pack_forget()
            self.panel.pack(fill="both", expand=True)
            self.configure(width=self._shown_width)
            self.app.coach_divider.grid()
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

    # ------------------------------------------------------------ width (divider)
    def set_width(self, width: int) -> None:
        """New preferred width from the divider; the bubbles re-wrap through <Configure>."""
        self.width = max(config.COACH_MIN_WIDTH, int(width))
        self._show(self.width)

    def fit(self, maximum: int) -> None:
        """The window changed size: show at most ``maximum`` px without forgetting the preferred width."""
        self._show(max(config.COACH_MIN_WIDTH, min(self.width, int(maximum))))

    def _show(self, width: int) -> None:
        if width != self._shown_width:
            self._shown_width = width
            if not self.collapsed:
                self.configure(width=width)

    def text_size_changed(self) -> None:
        """Fonts were resized in place (labels and bubbles re-measure themselves); keep the newest in view."""
        self._scroll_down()

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

    # ------------------------------------------------------------ wrapping
    def _on_messages_resize(self, _event: Any) -> None:
        self._rewrap_later()

    def rewrap_now(self) -> bool:
        """Re-wrap to the message list's current width now; True when anything changed."""
        self._rewrap_later.cancel()
        canvas = self.messages._parent_canvas  # noqa: SLF001
        if canvas.winfo_width() <= 1:
            return False
        return self.rewrap(int(canvas.winfo_width() / ctk.ScalingTracker.get_widget_scaling(self)))

    def _rewrap_keep_scroll(self) -> None:
        anchor = self.scroll_anchor()          # the texts still have their old wrapping here
        if self.rewrap_now():
            self.update_idletasks()
            self.restore_scroll(anchor)

    def rewrap(self, width: int) -> bool:
        """Fit every bubble text, badge line and card text to a message list ``width`` px wide."""
        if width < _MIN_WRAP or width == self._area:
            return False
        self._area = width
        self._wrapped = [(text, inset) for text, inset in self._wrapped if text.winfo_exists()]
        for text, inset in self._wrapped:
            text.configure(wraplength=self._wrap(inset))
        return True

    # ------------------------------------------------------------ scroll position
    def scroll_anchor(self) -> tuple[str, Optional[Any], int]:
        """What the list shows now: ("bottom", None, 0) when scrolled to the end, otherwise
        ("top", message widget at the top edge, px of it above the edge)."""
        canvas = self.messages._parent_canvas  # noqa: SLF001
        children = self.messages.winfo_children()
        if not children or canvas.yview()[1] >= 0.999:
            return ("bottom", None, 0)
        top = int(canvas.canvasy(0))
        for child in children:
            if child.winfo_y() + child.winfo_height() > top:
                return ("top", child, top - child.winfo_y())
        return ("bottom", None, 0)

    def restore_scroll(self, anchor: tuple[str, Optional[Any], int]) -> None:
        """Bring the scroll region up to date and show the anchor again: the bottom stays the bottom,
        otherwise the same message stays at the top edge; the view is clamped to the content, so the
        list is never scrolled past its end (blank)."""
        canvas = self.messages._parent_canvas  # noqa: SLF001
        box = canvas.bbox("all")
        if not box:
            return
        content = max(1, box[3] - box[1])
        view = max(1, canvas.winfo_height())
        canvas.configure(scrollregion=box)
        kind, child, offset = anchor
        target = content - view
        if kind == "top" and child is not None and child.winfo_exists():
            target = child.winfo_y() + max(0, min(offset, child.winfo_height() - 1))
        target = max(0, min(target, content - view))
        canvas.yview_moveto(target / content if content > view else 0.0)

    def _wrap(self, inset: int) -> int:
        return max(_MIN_WRAP, self._area - inset)

    def _text(self, master: object, text: str, inset: int, size: int = 13, weight: str = "normal",
              color: theme.Color = theme.TEXT, justify: str = "left") -> ctk.CTkLabel:
        """A label that wraps to the panel width (long words break too) and re-wraps when it changes."""
        widget = ctk.CTkLabel(master, text=text, font=fonts.chat(size, weight), text_color=color,
                              wraplength=self._wrap(inset), justify=justify,  # type: ignore[arg-type]
                              anchor="center" if justify == "center" else "w")
        self._wrapped.append((widget, inset))
        return widget

    def _bubble_text(self, master: object, text: str, bg: theme.Token) -> ChatText:
        """Bubble text: chat size, slightly looser line spacing, re-wraps like the labels."""
        widget = ChatText(master, text, fonts.chat(), self._wrap(_INSET_BUBBLE), theme.TEXT, bg)
        self._wrapped.append((widget, _INSET_BUBBLE))  # type: ignore[arg-type]
        return widget

    # ------------------------------------------------------------ messages
    def load_history(self) -> None:
        for child in self.messages.winfo_children():
            child.destroy()
        self._wrapped.clear()
        self._interactive.clear()
        self._undo_button = None
        self._rendered = 0
        history = self.app.coach.history(config.CHAT_RENDERED)
        self._oldest_id = history[0].id if history else 0
        self.has_older = len(history) == config.CHAT_RENDERED
        for message in history:
            self.add_message(message, live=False)
        self._scroll_down()

    def check_load_older(self) -> None:
        """Called by the app's poll: at the top of the list, render the next older messages."""
        if not self.has_older or self.collapsed or self._prepending or self._scroll_pending:
            return
        canvas = self.messages._parent_canvas  # noqa: SLF001
        first, last = canvas.yview()
        if canvas.winfo_height() > 1 and first <= 0.0 and last < 1.0 and self.messages.winfo_children():
            self.load_older()          # scrolled to the top of a list taller than the view

    def load_older(self) -> int:
        """Prepend up to CHAT_LOAD_MORE older messages; the message at the top edge stays where it is
        (applied atomically, so the list never jumps). Returns how many were added."""
        older = (self.app.coach.history(config.CHAT_LOAD_MORE, before_id=self._oldest_id)
                 if self._oldest_id else [])
        self.has_older = len(older) == config.CHAT_LOAD_MORE
        if not older:
            return 0
        self._oldest_id = older[0].id

        def prepend() -> None:
            first = self.messages.winfo_children()[0] if self.messages.winfo_children() else None
            self._prepending, self._insert_before = True, first
            latest = list(self._interactive)          # only the newest message keeps active buttons
            try:
                for message in older:
                    self.add_message(message, live=False)
            finally:
                self._prepending, self._insert_before = False, None
                for button in self._interactive:
                    if button not in latest and button.winfo_exists():
                        button.configure(state="disabled")
                self._interactive = latest
        self.app.apply_layout(prepend, anchor=("top", self.messages.winfo_children()[0], 0)
                              if self.messages.winfo_children() else None)
        return len(older)

    def _pack(self, widget: Any, **options: Any) -> None:
        """Pack a message row at the end, or before the first row while older messages are prepended."""
        if self._insert_before is not None:
            options["before"] = self._insert_before
        widget.pack(**options)

    def add_message(self, message: ChatMessage, live: bool = True) -> None:
        if message.role != "user" and not self._prepending:
            self._retire_interactive()
        if message.role == "user":
            self._bubble(message.text, theme.USER_BUBBLE, "e")
        elif message.role == "system_event":
            self._pack(self._text(self.messages, message.text, _INSET_EVENT, 13, color=theme.MUTED,
                                  justify="center"), fill="x", padx=10, pady=4)
        else:
            self._coach_message(message)
        self._rendered += 1
        if live:
            if self.collapsed and message.role != "user":
                self.unread += 1
                self._show_badge()
            self._scroll_down()

    def _bubble(self, text: str, color: theme.Token, anchor: str, tag: str = "") -> ctk.CTkFrame:
        holder = ctk.CTkFrame(self.messages, fg_color="transparent")
        self._pack(holder, fill="x", padx=6, pady=3)
        border = theme.COACH_BUBBLE_BORDER if color is theme.COACH_BUBBLE else color
        bubble = ctk.CTkFrame(holder, fg_color=color, corner_radius=12, border_width=1, border_color=border)
        bubble.pack(anchor=anchor, padx=(28, 0) if anchor == "e" else (0, 28))
        if tag:
            ctk.CTkLabel(bubble, text=tag, font=fonts.chat(10, "bold"), text_color=theme.ACCENT_TEXT,
                         height=14).pack(anchor="w", padx=10, pady=(6, 0))
        self._bubble_text(bubble, text, color).pack(anchor="w", padx=10, pady=(2 if tag else 8, 8))
        return bubble

    def _coach_message(self, message: ChatMessage) -> None:
        bubble = self._bubble(message.text, theme.COACH_BUBBLE, "w", "COACH" if message.proactive else "")
        payload = message.actions or {}
        actions = payload.get("actions") or []
        if actions:
            box = ctk.CTkFrame(bubble, fg_color="transparent")
            box.pack(fill="x", padx=8, pady=(0, 6))
            ctk.CTkLabel(box, text="ACTIONS EXECUTED", font=fonts.chat(10, "bold"), text_color=theme.MUTED,
                         height=16).pack(anchor="w", padx=2)
            for action in actions:
                ok = action.get("ok")
                self._text(box, ("✓ " if ok else "✕ ") + str(action.get("summary", "")), _INSET_BADGE, 13,
                           color=theme.SUCCESS if ok else theme.DANGER).pack(anchor="w", padx=2)
            if not self._prepending and any(a.get("ok") and a.get("action_id") and a.get("tool") != "undo_last"
                                            for a in actions):
                if self._undo_button is not None and self._undo_button.winfo_exists():
                    self._undo_button.destroy()          # Undo only on the latest action
                self._undo_button = ctk.CTkButton(
                    box, text="Undo", width=44, height=20, fg_color="transparent", hover_color=theme.SURFACE_3,
                    text_color=theme.ACCENT_TEXT, font=fonts.chat(12, "bold"), command=self._undo)
                self._undo_button.pack(anchor="w")
        for card in payload.get("confirmations") or []:
            box = ctk.CTkFrame(bubble, fg_color=theme.SURFACE_3, corner_radius=8)
            box.pack(fill="x", padx=8, pady=(0, 8))
            self._text(box, card.get("preview", "Confirm?"), _INSET_CARD, 14, "bold").pack(
                anchor="w", padx=8, pady=(8, 4))
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
                                       border_color=theme.ACCENT, text_color=theme.ACCENT_TEXT,
                                       text_color_disabled=theme.DISABLED, font=fonts.chat(12, "bold"),
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
            self._scroll_pending = False
            with contextlib.suppress(Exception):       # the panel may already be destroyed
                self.update_idletasks()
                self.restore_scroll(("bottom", None, 0))
        self._scroll_pending = True
        self.after(60, move)
