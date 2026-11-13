"""The Qt app container: main window, navigation, wiring between EventBus, ToolExecutor, Coach and views.

Threading rule: worker threads never touch widgets. Everything they report (bus events, connection test
results) goes through ``post_ui`` → a queued Qt signal → the GUI thread."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Optional

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import config, events
from ..coach.coach import Coach
from ..coach.providers import Provider
from ..db.repository import Repository
from ..events import Event, EventBus
from ..models import ToolResult
from ..timer import PomodoroTimer
from ..tools.executor import ToolExecutor
from .coach_panel import CoachPanel
from .style import LOOK, set_prop
from .views import BaseView
from .views.reviews import ReviewsView
from .views.settings import SettingsView
from .views.subjects import SubjectsView
from .views.today import TodayView
from .views.week import WeekView
from .widgets import Splitter, button, label

log = logging.getLogger(__name__)
VIEW_NAMES = ["Today", "Week", "Subjects", "Reviews", "Settings"]
_VIEW_CLASSES: dict[str, type[BaseView]] = {"Today": TodayView, "Week": WeekView, "Subjects": SubjectsView,
                                            "Reviews": ReviewsView, "Settings": SettingsView}
SIDEBAR_WIDTH = 168
SAVE_DELAY_MS = 400          # layout state is saved this long after the last splitter move / window resize


class QtDispatcher(QObject):
    """``post(fn)`` from any thread runs ``fn`` on the GUI thread (queued signal)."""
    _call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def post(self, fn: Callable[[], None]) -> None:
        self._call.emit(fn)

    @staticmethod
    def _run(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - a failing UI task must not take the app down
            log.exception("ui task failed")


def ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication([])
        app.setApplicationName(config.APP_NAME)
    return app  # type: ignore[return-value]


class MainWindow(QMainWindow):
    def __init__(self, repo: Repository, today_fn: Callable[[], date] = date.today,
                 provider: Optional[Provider] = None, run_startup: bool = True) -> None:
        self.qapp = ensure_app()
        LOOK.theme = config.normalize_theme(repo.get_setting("theme", config.THEME_NAMES[0]) or "")
        size = repo.get_setting("text_size", "Medium")
        LOOK.text_size = size if size in config.TEXT_SIZE_NAMES else "Medium"
        LOOK.apply(self.qapp)
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        if config.icon_path().is_file():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(config.icon_path())))
        self.setMinimumSize(1024, 700)

        self.today = today_fn
        self._day = today_fn()
        self.dispatcher = QtDispatcher()
        self.bus = EventBus()
        self.executor = ToolExecutor(repo, self.bus, today_fn)
        self.coach = Coach(repo, self.executor, self.bus, provider)
        self.timer = PomodoroTimer(on_focus_end=self._focus_ended,
                                   on_break_start=lambda: self.bus.emit(events.BREAK_STARTED))
        self._settings: dict[str, str] = {}
        self._refresh_pending = False
        self._closing = False
        self._close_asked = False
        self.current = ""

        # ---- layout: sidebar | splitter(centre stack | coach panel)
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        self.splitter = Splitter()
        self.centre = QWidget()
        self.centre.setObjectName("centre")
        centre_layout = QVBoxLayout(self.centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)
        self.stack = QStackedWidget()
        centre_layout.addWidget(self.stack, 1)
        self.toast_label = label("", "muted")
        self.toast_label.setContentsMargins(18, 0, 18, 6)
        self.toast_label.setMinimumWidth(10)
        centre_layout.addWidget(self.toast_label)
        self.views: dict[str, BaseView] = {}
        for name in VIEW_NAMES:
            view = _VIEW_CLASSES[name](self)
            self.stack.addWidget(view)
            self.views[name] = view
        self.panel = CoachPanel(self)
        self.splitter.addWidget(self.centre)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(root)

        self._save_layout = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS, timeout=self.save_layout)
        self.splitter.splitterMoved.connect(lambda *_: self._save_layout.start())
        self.splitter.reset_requested.connect(self.reset_splitter)
        self._restore_layout()
        self.panel.load_history()

        for keys, step in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(lambda s=step: self.step_text_size(s))

        self.bus.subscribe(events.STATE_CHANGED, self._on_state_changed)
        self.bus.subscribe(events.CHAT_MESSAGE, self._on_chat_message)
        self.bus.subscribe(events.COACH_BUSY, self._on_busy)
        self.show_view("Today")
        self._tick_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._tick_timer.start()
        self._minute_timer = QTimer(self, interval=60_000, timeout=self._minute)
        self._minute_timer.start()
        if run_startup:
            QTimer.singleShot(250, self._startup)

    # ------------------------------------------------------------ layout
    def _build_sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(10, 18, 10, 12)
        layout.setSpacing(2)
        brand = label("NeuroPlan", "brand")
        brand.setContentsMargins(8, 0, 0, 0)
        layout.addWidget(brand)
        tagline = label("study planner", "small")
        tagline.setContentsMargins(8, 0, 0, 14)
        layout.addWidget(tagline)
        self.nav: dict[str, Any] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, name in enumerate(VIEW_NAMES):
            nav = button(name, lambda n=name: self.show_view(n), kind="nav",
                         tooltip=f"{name} (Ctrl+{i + 1})")
            nav.setCheckable(True)
            group.addButton(nav)
            layout.addWidget(nav)
            self.nav[name] = nav
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self).activated.connect(lambda n=name: self.show_view(n))
        layout.addStretch(1)
        self.theme_button = button("", self.cycle_theme, tooltip="Switch theme")
        layout.addWidget(self.theme_button)
        self.mode_label: QLabel = label("", "small", wrap=True)
        self.mode_label.setContentsMargins(8, 6, 0, 0)
        layout.addWidget(self.mode_label)
        self._label_theme_button()
        return bar

    def default_sizes(self) -> list[int]:
        total = max(1, self.splitter.width() or self.width() - SIDEBAR_WIDTH)
        coach = config.COACH_DEFAULT_WIDTH
        return [max(1, total - coach - self.splitter.handleWidth()), coach]

    def reset_splitter(self) -> None:
        """Double-click on the handle: back to the default coach width."""
        if not self.panel.collapsed:
            self.splitter.setSizes(self.default_sizes())
            self.save_layout()

    def _restore_layout(self) -> None:
        geometry = self.setting("window_geometry")
        if not (geometry and self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))):
            self.resize(1280, 800)
        state = self.setting("splitter_main")
        if not (state and self.splitter.restoreState(QByteArray.fromBase64(state.encode()))):
            self.splitter.setSizes(self.default_sizes())
        self.panel.setMinimumWidth(config.COACH_MIN_WIDTH)

    def save_layout(self) -> None:
        if self._closing and not self.isVisible():
            return
        prefs: dict[str, Any] = {"window_geometry": bytes(self.saveGeometry().toBase64()).decode()}
        if not self.panel.collapsed:
            prefs["splitter_main"] = bytes(self.splitter.saveState().toBase64()).decode()
        self.save_prefs(prefs)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "_save_layout"):
            self._save_layout.start()

    def moveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().moveEvent(event)
        if hasattr(self, "_save_layout"):
            self._save_layout.start()

    def show_view(self, name: str) -> None:
        if name not in self.views:
            return
        self.current = name
        self.nav[name].setChecked(True)
        view = self.views[name]
        self.stack.setCurrentWidget(view)
        if isinstance(view, SettingsView):
            view.loaded = False          # entering Settings always shows the stored values
        self._refresh_view(view)
        self.mode_label.setText(self.coach.status_text())

    def _refresh_view(self, view: BaseView) -> None:
        try:
            view.refresh()
        except Exception as exc:  # noqa: BLE001 - a broken view must not take the app down
            log.exception("refresh of %s failed", type(view).__name__)
            self.toast(f"Could not refresh {view.title}: {exc}", error=True)

    def refresh_current(self) -> None:
        if self.current:
            self._refresh_view(self.views[self.current])

    # ------------------------------------------------------------ look
    def _label_theme_button(self) -> None:
        self.theme_button.setText(f"◐  {LOOK.theme}")

    def set_theme(self, name: str, store: bool = True) -> None:
        name = config.normalize_theme(name)
        if store:
            self.save_prefs({"theme": name})
        if name != LOOK.theme:
            LOOK.theme = name
            self._apply_look()

    def cycle_theme(self) -> None:
        names = list(config.THEME_NAMES)
        self.set_theme(names[(names.index(LOOK.theme) + 1) % len(names)])

    def set_text_size(self, name: str, store: bool = True) -> None:
        if name not in config.TEXT_SIZE_NAMES:
            return
        if store:
            self.save_prefs({"text_size": name})
        if name != LOOK.text_size:
            LOOK.text_size = name
            self._apply_look()

    def step_text_size(self, step: int) -> None:
        names = list(config.TEXT_SIZE_NAMES)
        if step == 0:
            target = "Medium"
        else:
            target = names[max(0, min(len(names) - 1, names.index(LOOK.text_size) + step))]
        self.set_text_size(target)

    def _apply_look(self) -> None:
        LOOK.apply(self.qapp)
        self._label_theme_button()
        self.panel.look_changed()
        for view in self.views.values():
            view.look_changed()

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

    def save_prefs(self, prefs: dict[str, Any]) -> bool:
        result = self.executor.execute("set_ui_prefs", {"prefs": prefs}, "ui")
        if not result.ok:
            self.toast(result.summary, error=True)
        elif self._settings:
            self._settings.update({k: str(v) for k, v in prefs.items()})
        return result.ok

    def settings_changed(self) -> None:
        self._settings = {}
        self.set_theme(self.setting("theme"), store=False)
        self.set_text_size(self.setting("text_size"), store=False)
        self.coach.reload_provider()
        self.panel.set_status()
        self.mode_label.setText(self.coach.status_text())

    def ask_coach(self, text: str) -> None:
        self.panel.expand()
        self.coach.submit(text)

    def toast(self, text: str, error: bool = False) -> None:
        self.toast_label.setText(text)
        set_prop(self.toast_label, "toast", "error" if error else "")
        QTimer.singleShot(6000, lambda t=text: self.toast_label.text() == t and self.toast_label.setText(""))

    def post_ui(self, fn: Callable[[], None]) -> None:
        """Thread-safe: run ``fn`` on the GUI thread."""
        self.dispatcher.post(fn)

    # ------------------------------------------------------------ event plumbing (any thread)
    def _on_state_changed(self, event: Event) -> None:
        self.post_ui(self._schedule_refresh)

    def _schedule_refresh(self) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._do_refresh)

    def _do_refresh(self) -> None:
        self._refresh_pending = False
        self._settings = {}
        self.refresh_current()

    def _on_chat_message(self, event: Event) -> None:
        message = event.payload["message"]
        self.post_ui(lambda: self.panel.add_message(message))

    def _on_busy(self, event: Event) -> None:
        busy = bool(event.payload.get("busy"))
        self.post_ui(lambda: self.panel.set_busy(busy))

    # ------------------------------------------------------------ periodic work
    def _startup(self) -> None:
        if not self._closing:
            self.coach.startup()

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
            self._schedule_refresh()
        self.coach.check_end_of_day()

    def _focus_ended(self) -> None:
        """Timer callback: show the confirmation card on Today. Nothing is logged yet."""
        self.show_view("Today")
        QApplication.beep()

    # ------------------------------------------------------------ closing
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if not self._close_asked:
            self._close_asked = True
            try:
                if self.coach.on_close():           # end-of-day check-in first; closing again exits
                    self.panel.expand()
                    event.ignore()
                    return
            except Exception:  # noqa: BLE001
                log.exception("close check-in failed")
        self.save_layout()
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._tick_timer.stop()
        self._minute_timer.stop()
        self._save_layout.stop()
        self.coach.shutdown()
