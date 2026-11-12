"""The app container: window, navigation, wiring between EventBus, ToolExecutor, Coach and views.

Threading rule: background threads never touch widgets. They call ``post_ui`` and the main loop
picks the work up from a queue through ``after``."""
from __future__ import annotations

import contextlib
import logging
import queue
import time
import tkinter
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
from . import fonts, theme, win32
from .coach_panel import CoachPanel
from .views import BaseView
from .views.reviews import ReviewsView
from .views.settings import SettingsView
from .views.subjects import SubjectsView
from .views.today import TodayView
from .views.week import WeekView
from .widgets.debounce import Debounce
from .widgets.divider import Divider

log = logging.getLogger(__name__)
VIEW_NAMES = ["Today", "Week", "Subjects", "Reviews", "Settings"]
_VIEW_CLASSES: dict[str, type[BaseView]] = {"Today": TodayView, "Week": WeekView, "Subjects": SubjectsView,
                                            "Reviews": ReviewsView, "Settings": SettingsView}


class NeuroPlanApp(ctk.CTk):
    def __init__(self, repo: Repository, today_fn: Callable[[], date] = date.today,
                 provider: Optional[Provider] = None, run_startup: bool = True) -> None:
        fonts.BOOK.reset()                                   # fonts of an earlier window belong to its Tk root
        fonts.BOOK.text_size = _choice(repo.get_setting("text_size", "Medium"), config.TEXT_SIZE_NAMES, "Medium")
        theme.apply_appearance(repo.get_setting("theme", config.THEME_NAMES[0]) or config.THEME_NAMES[0])
        super().__init__()
        self.title(config.APP_NAME)
        self.geometry("1280x800")
        self.minsize(self.window_min_width(), config.WINDOW_MIN_HEIGHT)
        self.configure(fg_color=theme.BG)
        if config.icon_path().is_file():           # otherwise CustomTkinter's default icon stays
            with contextlib.suppress(Exception):   # an unreadable icon must not stop the app
                self.iconbitmap(str(config.icon_path()))

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
        self.centre = centre
        centre.grid_rowconfigure(0, weight=1)
        centre.grid_columnconfigure(0, weight=1)
        self.toast_label = ctk.CTkLabel(centre, text="", font=theme.font(12), text_color=theme.MUTED, anchor="w",
                                        height=22)
        self.toast_label.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 6))
        self.views: dict[str, BaseView] = {}
        for name in VIEW_NAMES:
            view = _VIEW_CLASSES[name](centre, self)
            view.grid(row=0, column=0, sticky="nsew")
            view.grid_remove()          # only the visible view is gridded, so a resize redraws only that one
            self.views[name] = view
        self.panel = CoachPanel(self, self, self.pane_width("pane_coach_width"))
        self.coach_divider = Divider(self, get_width=lambda: self.panel.width,
                                     on_apply=lambda w: self.apply_layout(
                                         lambda: (self.coach_divider.hide_line(), self.panel.set_width(w))),
                                     on_commit=lambda w: self.save_pane("pane_coach_width", w),
                                     on_reset=self._reset_coach_width, minimum=lambda: config.COACH_MIN_WIDTH,
                                     maximum=self._coach_max_width, grows_left=True)
        self.coach_divider.grid(row=0, column=2, sticky="ns")
        self.panel.grid(row=0, column=3, sticky="ns")
        self.panel.load_history()
        # window resize / maximise / restore: fit the panes once the burst of <Configure> events is over
        self._fit_panes = Debounce(self, lambda: self.apply_layout(self._fit_coach))
        self.bind("<Configure>", self._on_window_resize, add="+")
        self.bind("<Deactivate>", lambda _e: self.cancel_drags(), add="+")   # focus lost: drop a drag
        self.bind_all("<Escape>", lambda _e: self.cancel_drags(), add="+")
        self.layout_ms = 0.0                  # duration of the last atomic layout pass (dev tools read it)
        self._cover: Optional[win32.Cover] = None                  # created once the window exists
        self._max_cover: Optional[win32.MaximiseCover] = None
        for sequence, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1),
                               ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1), ("<Control-0>", 0),
                               ("<Control-KP_0>", 0)):
            self.bind_all(sequence, lambda _e, s=step: self.step_text_size(s), add="+")

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
        self.mode_label.pack(side="bottom", anchor="w", padx=18, pady=(4, 14))
        self.theme_button = ctk.CTkButton(bar, text="", anchor="w", height=28, corner_radius=8,
                                          fg_color="transparent", hover_color=theme.SURFACE_2, border_width=1,
                                          border_color=theme.BORDER, text_color=theme.MUTED,
                                          font=theme.font(11, "bold"), command=self.cycle_theme)
        self.theme_button.pack(side="bottom", fill="x", padx=10)
        self._label_theme_button()

    # ------------------------------------------------------------ look: theme, text size, dividers
    def _label_theme_button(self) -> None:
        self.theme_button.configure(text=f"◐  {theme.current_theme()}")

    def set_theme(self, name: str, store: bool = True) -> None:
        """Switch the theme live (no restart) and remember it."""
        name = config.normalize_theme(name)
        if store:
            self.save_prefs({"theme": name})
        if theme.set_theme(name):
            self._label_theme_button()
            for view in self.views.values():
                view.theme_changed()

    def cycle_theme(self) -> None:
        self.set_theme(theme.next_theme(theme.current_theme()))

    def set_text_size(self, name: str, store: bool = True) -> None:
        if name not in config.TEXT_SIZE_NAMES:
            return
        if store:
            self.save_prefs({"text_size": name})
        if fonts.set_text_size(name):
            self.minsize(self.window_min_width(), config.WINDOW_MIN_HEIGHT)
            self.apply_layout(self._fit_coach)       # a larger text needs a wider centre view
            self.panel.text_size_changed()

    def step_text_size(self, step: int) -> str:
        """Ctrl+Plus / Ctrl+Minus / Ctrl+0."""
        self.set_text_size(fonts.step_text_size(fonts.text_size(), step))
        return "break"

    def save_prefs(self, prefs: dict[str, Any]) -> bool:
        result = self.executor.execute("set_ui_prefs", {"prefs": prefs}, "ui")
        if not result.ok:
            self.toast(result.summary, error=True)
        elif self._settings:
            self._settings.update({k: str(v) for k, v in prefs.items()})
        return result.ok

    def pane_width(self, key: str) -> int:
        default = config.PANE_SETTINGS[key]
        try:
            return max(config.COLUMN_MIN_WIDTH, int(self.setting(key) or default))
        except ValueError:
            return default

    def save_pane(self, key: str, width: int) -> None:
        self.save_prefs({key: int(width)})

    def _scale(self) -> float:
        return float(ctk.ScalingTracker.get_widget_scaling(self))

    def _coach_max_width(self) -> int:
        """The coach panel may grow until the centre view is down to its minimum width."""
        window = self.winfo_width() / self._scale()
        if window <= 1:
            return config.PANE_MAX_WIDTH
        room = window - theme.SIDEBAR_WIDTH - config.DIVIDER_WIDTH - self.centre_min_width()
        return max(config.COACH_MIN_WIDTH, int(room))

    @staticmethod
    def centre_min_width() -> int:
        """The narrowest centre view that still shows the Today cards unclipped, for the text size."""
        return config.CENTRE_MIN_WIDTHS[fonts.text_size()]

    def window_min_width(self) -> int:
        """Sidebar + divider + the narrowest coach panel + the narrowest centre view: below this the
        window cannot be made smaller (the coach panel gives way first, then the window stops)."""
        return theme.SIDEBAR_WIDTH + config.DIVIDER_WIDTH + config.COACH_MIN_WIDTH + self.centre_min_width()

    def _reset_coach_width(self) -> None:
        self.apply_layout(lambda: self.panel.set_width(config.COACH_DEFAULT_WIDTH))
        self.save_pane("pane_coach_width", config.COACH_DEFAULT_WIDTH)

    def _fit_coach(self) -> None:
        self.panel.fit(self._coach_max_width())

    def dividers(self) -> list[Divider]:
        subjects = self.views.get("Subjects")
        return [self.coach_divider] + ([subjects.divider] if subjects is not None else [])  # type: ignore[attr-defined]

    def cancel_drags(self) -> None:
        for divider in self.dividers():
            divider.cancel()

    # ------------------------------------------------------------ atomic layout changes
    def layout_cover(self) -> Optional[win32.Cover]:
        """The snapshot cover of this window (Windows only, once the window exists)."""
        if self._cover is None and win32.IS_WINDOWS:
            hwnd = win32.toplevel_hwnd(self)
            if hwnd:
                self._cover = win32.Cover(hwnd)
        return self._cover

    def apply_layout(self, change: Callable[[], None], already_covered: bool = False,
                     anchor: Optional[tuple[str, Any, int]] = None) -> None:
        """Apply a layout change so the user sees the old layout, then the new one, nothing in between.

        A snapshot of the window covers it (see ``win32.covered``) while the change, every re-wrap it
        causes, the chat scroll restoration and the repaint run in one synchronous pass underneath; then
        the cover is removed."""
        start = time.perf_counter()
        anchor = anchor or self.panel.scroll_anchor()
        cover = None if already_covered else self.layout_cover()
        held, Debounce.held = Debounce.held, True      # no debounce timer fires inside the pass
        try:
            lines = tuple(line for line in (d.ghost_line() for d in self.dividers()) if line is not None)
            with win32.covered(self, cover, lines):
                change()
                self.settle_layout()
                self.panel.restore_scroll(anchor)
                self.settle_layout()
                self._drain_events()         # everything painted underneath before the cover goes
        finally:
            Debounce.held = held
            if not held:
                Debounce.release_held()
        self.layout_ms = (time.perf_counter() - start) * 1000

    def settle_layout(self, rounds: int = 8) -> None:
        """Run geometry, <Configure> handlers, pending debounced re-wraps and the chat re-wrap until
        nothing changes any more."""
        for _ in range(rounds):
            self.update_idletasks()
            busy = self._drain_events() > 0
            busy = self.panel.rewrap_now() or busy           # (also drops the panel's pending re-wrap)
            busy = Debounce.flush_all() > 0 or busy
            if not busy:
                break

    def _drain_events(self, limit: int = 20000) -> int:
        """Process every event that is ready now (no waiting); returns how many ran."""
        flags = tkinter._tkinter.ALL_EVENTS | tkinter._tkinter.DONT_WAIT  # type: ignore[attr-defined]
        count = 0
        while count < limit and self.tk.dooneevent(flags):
            count += 1
        return count

    def _on_window_resize(self, event: Any) -> None:
        if event.widget is not self:          # the toplevel tag also sees every child's <Configure>
            return
        cover = self.layout_cover()
        if self._max_cover is None and cover is not None:
            self._max_cover = win32.MaximiseCover(cover.owner, cover, lambda: theme.resolve(theme.BG))
        if self._max_cover is not None and self._max_cover.pending:
            # maximise / restore: the cover went up before Windows resized the window; settle the new
            # layout underneath in one pass, then show it
            self._fit_panes.cancel()
            try:
                self.apply_layout(self._fit_coach, already_covered=True)
            finally:
                self._max_cover.release()
            return
        self._fit_panes()                     # a window edge being dragged: fit once the burst is over

    def show_view(self, name: str) -> None:
        if name not in self.views:
            return
        self.current = name
        for key, button in self.nav.items():
            button.configure(fg_color=theme.SURFACE_3 if key == name else "transparent")
        view = self.views[name]
        for other in self.views.values():
            if other is not view:
                other.grid_remove()
        view.grid()
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
        self.set_theme(self.setting("theme"), store=False)
        self.set_text_size(self.setting("text_size"), store=False)
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
        self.panel.check_load_older()

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
        self._fit_panes.cancel()
        if self._max_cover is not None:
            self._max_cover.remove()
            self._max_cover = None
        if self._cover is not None:
            self._cover.destroy()
            self._cover = None
        for after_id in list(self._after_ids.values()) + ([self._toast_after] if self._toast_after else []):
            with contextlib.suppress(Exception):
                self.after_cancel(after_id)
        self.coach.shutdown()
        super().destroy()


def _choice(value: Optional[str], allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default
