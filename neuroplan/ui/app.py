"""The app container: window, navigation, wiring between EventBus, ToolExecutor, Coach and views.

Threading rule: background threads never touch widgets. They call ``post_ui`` and the main loop
picks the work up from a queue through ``after``."""
from __future__ import annotations

import contextlib
import logging
import queue
from datetime import date
from typing import Any, Callable, Optional

import customtkinter as ctk

from .. import config, events
from ..coach.coach import Coach
from ..coach.providers import Provider
from ..db.repository import Repository
from ..events import Event, EventBus
from ..models import ToolResult
from ..timer import PomodoroTimer
from ..tools.executor import ToolExecutor
from . import theme
from .coach_panel import CoachPanel
from .views import BaseView
from .views.reviews import ReviewsView
from .views.settings import SettingsView
from .views.subjects import SubjectsView
from .views.today import TodayView
from .views.week import WeekView

log = logging.getLogger(__name__)
VIEW_NAMES = ["Today", "Week", "Subjects", "Reviews", "Settings"]
_VIEW_CLASSES: dict[str, type[BaseView]] = {"Today": TodayView, "Week": WeekView, "Subjects": SubjectsView,
                                            "Reviews": ReviewsView, "Settings": SettingsView}


class NeuroPlanApp(ctk.CTk):
    def __init__(self, repo: Repository, today_fn: Callable[[], date] = date.today,
                 provider: Optional[Provider] = None, run_startup: bool = True) -> None:
        theme.apply_appearance(repo.get_setting("theme", "Dark") or "Dark")
        super().__init__()
        self.title(config.APP_NAME)
        self.geometry("1280x800")
        self.minsize(1024, 700)
        self.configure(fg_color=theme.BG)

        self.today = today_fn
        self._day = today_fn()
        self.bus = EventBus()
        self.executor = ToolExecutor(repo, self.bus, today_fn)
        self.coach = Coach(repo, self.executor, self.bus, provider)
        self.timer = PomodoroTimer(on_focus_end=self._focus_ended,
                                   on_break_start=lambda: self.bus.emit(events.BREAK_STARTED))
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._after_ids: dict[str, str] = {}
        self._needs_refresh = False
        self._closing = False
        self._close_asked = False
        self._settings: dict[str, str] = {}
        self._toast_after: Optional[str] = None
        self.current = ""

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        centre = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0)
        centre.grid(row=0, column=1, sticky="nsew")
        centre.grid_rowconfigure(0, weight=1)
        centre.grid_columnconfigure(0, weight=1)
        self.toast_label = ctk.CTkLabel(centre, text="", font=theme.font(12), text_color=theme.MUTED, anchor="w",
                                        height=22)
        self.toast_label.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 6))
        self.views: dict[str, BaseView] = {}
        for name in VIEW_NAMES:
            view = _VIEW_CLASSES[name](centre, self)
            view.grid(row=0, column=0, sticky="nsew")
            self.views[name] = view
        self.panel = CoachPanel(self, self)
        self.panel.grid(row=0, column=2, sticky="ns")
        self.panel.load_history()

        self.bus.subscribe(events.STATE_CHANGED, self._on_state_changed)
        self.bus.subscribe(events.CHAT_MESSAGE, self._on_chat_message)
        self.bus.subscribe(events.COACH_BUSY, self._on_busy)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_view("Today")
        self._schedule("poll", 40, self._poll)
        self._schedule("tick", 1000, self._tick)
        self._schedule("minute", 60_000, self._minute)
        if run_startup:
            self._schedule("startup", 250, self.coach.startup, repeat=False)

    # ------------------------------------------------------------ layout
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=theme.SIDEBAR, corner_radius=0, width=theme.SIDEBAR_WIDTH)
        bar.grid(row=0, column=0, sticky="ns")
        bar.grid_propagate(False)
        bar.pack_propagate(False)
        theme.label(bar, "NeuroPlan", 19, "bold").pack(anchor="w", padx=18, pady=(20, 2))
        theme.label(bar, "study planner", 11, color=theme.MUTED).pack(anchor="w", padx=18, pady=(0, 18))
        self.nav: dict[str, ctk.CTkButton] = {}
        for name in VIEW_NAMES:
            button = ctk.CTkButton(bar, text=name, anchor="w", height=36, corner_radius=8, fg_color="transparent",
                                   hover_color=theme.SURFACE_2, text_color=theme.TEXT, font=theme.font(13, "bold"),
                                   command=lambda n=name: self.show_view(n))
            button.pack(fill="x", padx=10, pady=2)
            self.nav[name] = button
        self.mode_label = theme.label(bar, "", 10, color=theme.MUTED, wraplength=theme.SIDEBAR_WIDTH - 30,
                                      justify="left")
        self.mode_label.pack(side="bottom", anchor="w", padx=18, pady=14)

    def show_view(self, name: str) -> None:
        if name not in self.views:
            return
        self.current = name
        for key, button in self.nav.items():
            button.configure(fg_color=theme.SURFACE_3 if key == name else "transparent")
        view = self.views[name]
        view.tkraise()
        if isinstance(view, SettingsView):
            view._loaded = False        # entering Settings always shows the stored values
        self._refresh_view(view)
        self.mode_label.configure(text=self.coach.status_text())

    def _refresh_view(self, view: BaseView) -> None:
        try:
            view.refresh()
        except Exception as exc:  # a broken view must not take the app down
            log.exception("refresh of %s failed", type(view).__name__)
            self.toast(f"Could not refresh {view.title}: {exc}", error=True)

    def refresh_current(self) -> None:
        if self.current:
            self._refresh_view(self.views[self.current])

    # ------------------------------------------------------------ services for views
    def call(self, tool: str, args: Optional[dict[str, Any]] = None) -> ToolResult:
        """Run a write tool for the UI. Confirmation requests become a card in the coach panel."""
        result = self.executor.execute(tool, args or {}, "ui")
        if result.needs_confirmation:
            self.coach.request_confirmation(tool, result.data["args"], result.preview)
            self.panel.expand()
        elif not result.ok:
            self.toast(result.summary, error=True)
        elif result.summary and tool != "lock_block":
            self.toast(result.summary)
        return result

    def read(self, tool: str, args: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        result = self.executor.execute(tool, args or {}, "ui")
        if not result.ok or not isinstance(result.data, dict):
            self.toast(result.summary, error=True)
            return {}
        return result.data

    def setting(self, key: str) -> str:
        if not self._settings:
            self._settings = {k: str(v) for k, v in self.read("get_settings").items()}
        return self._settings.get(key, config.DEFAULT_SETTINGS.get(key, ""))

    def settings_changed(self) -> None:
        self._settings = {}
        theme.apply_appearance(self.setting("theme"))
        self.coach.reload_provider()
        self.panel.set_status()
        self.mode_label.configure(text=self.coach.status_text())

    def ask_coach(self, text: str) -> None:
        self.panel.expand()
        self.coach.submit(text)

    def toast(self, text: str, error: bool = False) -> None:
        self.toast_label.configure(text=text, text_color=theme.DANGER if error else theme.MUTED)
        if self._toast_after is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._toast_after)
        if not self._closing:
            self._toast_after = self.after(6000, lambda: self.toast_label.configure(text=""))

    def post_ui(self, fn: Callable[[], None]) -> None:
        """Thread-safe: run ``fn`` on the UI thread at the next poll."""
        self._ui_queue.put(fn)

    # ------------------------------------------------------------ event plumbing
    def _on_state_changed(self, event: Event) -> None:
        self.post_ui(self._mark_dirty)

    def _mark_dirty(self) -> None:
        self._needs_refresh = True

    def _on_chat_message(self, event: Event) -> None:
        message = event.payload["message"]
        self.post_ui(lambda: self.panel.add_message(message))

    def _on_busy(self, event: Event) -> None:
        busy = bool(event.payload.get("busy"))
        self.post_ui(lambda: self.panel.set_busy(busy))

    def process_ui_queue(self) -> None:
        """Drain work posted by other threads, then refresh the visible view if the state changed."""
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("ui task failed")
        if self._needs_refresh:
            self._needs_refresh = False
            self._settings = {}
            self.refresh_current()

    # ------------------------------------------------------------ periodic work (all through after())
    def _schedule(self, name: str, ms: int, fn: Callable[[], None], repeat: bool = True) -> None:
        def run() -> None:
            if self._closing:
                return
            try:
                fn()
            except Exception:
                log.exception("periodic task %s failed", name)
            if repeat and not self._closing:
                self._after_ids[name] = self.after(ms, run)
        self._after_ids[name] = self.after(ms, run)

    def _poll(self) -> None:
        self.process_ui_queue()

    def _tick(self) -> None:
        if self.timer.running:
            self.timer.tick(1)
        if self.current == "Today":
            self.views["Today"].tick()  # type: ignore[attr-defined]

    def _minute(self) -> None:
        today = self.today()
        if today != self._day:                      # the app stayed open past midnight
            self._day = today
            self.coach.startup()
            self._needs_refresh = True
        self.coach.check_end_of_day()

    def _focus_ended(self) -> None:
        """Timer callback (UI thread): show the confirmation card. Nothing is logged yet."""
        self.show_view("Today")
        with contextlib.suppress(Exception):
            self.bell()

    # ------------------------------------------------------------ closing
    def _on_close(self) -> None:
        if not self._close_asked:
            self._close_asked = True
            try:
                if self.coach.on_close():           # end-of-day check-in first; closing again exits
                    self.panel.expand()
                    return
            except Exception:
                log.exception("close check-in failed")
        self.destroy()

    def destroy(self) -> None:
        self._closing = True
        for after_id in list(self._after_ids.values()) + ([self._toast_after] if self._toast_after else []):
            with contextlib.suppress(Exception):
                self.after_cancel(after_id)
        self.coach.shutdown()
        super().destroy()
