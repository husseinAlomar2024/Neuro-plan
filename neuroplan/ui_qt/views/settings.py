"""Settings: provider and models, API key (keyring), proactivity, appearance, planner, data tools."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ... import config
from ..style import set_prop
from ..widgets import button, card, combo, field, label, line_edit, scroll_area, spinbox
from . import BaseView

if TYPE_CHECKING:
    from ..app import MainWindow

KEY_PLACEHOLDER = "•••••••• (saved)"


class SettingsView(BaseView):
    title = "Settings"

    def __init__(self, app: "MainWindow") -> None:
        super().__init__(app)
        self.loaded = False
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(10)
        self.getters: dict[str, Callable[[], str]] = {}
        self.setters: dict[str, Callable[[str], None]] = {}
        content.addWidget(self._build_coach())
        content.addWidget(self._build_planner())
        content.addWidget(self._build_data())
        content.addStretch(1)
        self.root.addWidget(scroll_area(body), 1)

    # ------------------------------------------------------------ building
    def _text(self, key: str, widget: Any) -> Any:
        self.getters[key] = lambda: widget.text().strip()
        self.setters[key] = lambda v: widget.setText(v)
        return widget

    def _choice(self, key: str, widget: Any) -> Any:
        self.getters[key] = widget.currentText
        self.setters[key] = lambda v: widget.setCurrentText(v)
        return widget

    def _number(self, key: str, widget: Any) -> Any:
        self.getters[key] = lambda: str(widget.value())
        self.setters[key] = lambda v: widget.setValue(int(v) if str(v).isdigit() else widget.minimum())
        return widget

    def _build_coach(self) -> QFrame:
        frame, layout = card(margins=(14, 12, 14, 14), spacing=8)
        layout.addWidget(label("Coach", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.provider = self._choice("provider", combo(list(config.PROVIDERS), on_change=lambda _v: self._key_hint()))
        grid.addWidget(field("Provider", self.provider), 0, 0)
        grid.addWidget(field("Chat model", self._text("chat_model", line_edit())), 0, 1)
        grid.addWidget(field("Background model", self._text("background_model", line_edit())), 0, 2)
        grid.addWidget(field("Base URL (OpenAI-compatible, e.g. http://localhost:11434/v1)",
                             self._text("base_url", line_edit(placeholder="https://…/v1"))), 1, 0, 1, 2)
        grid.addWidget(field("Proactive level",
                             self._choice("proactive_level", combo(list(config.PROACTIVE_LEVELS)))), 1, 2)
        self.key_edit = line_edit(password=True, placeholder="paste the key here")
        grid.addWidget(field("API key (stored in the OS keyring, never in the database)", self.key_edit), 2, 0, 1, 2)
        grid.addWidget(field("Daily AI call cap",
                             self._number("daily_llm_call_cap", spinbox(60, step=10, maximum=10000))), 2, 2)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.save_key_button = button("Save key", self._save_key, tooltip="Save the key to the OS keyring")
        self.test_button = button("Test connection", self._test, tooltip="Send one short request to the provider")
        row.addWidget(self.save_key_button)
        row.addWidget(self.test_button)
        self.key_status = label("", "muted", wrap=True)
        row.addWidget(self.key_status, 1)
        layout.addLayout(row)
        return frame

    def _build_planner(self) -> QFrame:
        frame, layout = card(margins=(14, 12, 14, 14), spacing=8)
        layout.addWidget(label("Planner and appearance", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.theme = self._choice("theme", combo(list(config.THEME_NAMES), on_change=self.app.set_theme))
        self.text_size = self._choice("text_size", combo(list(config.TEXT_SIZE_NAMES),
                                                         on_change=self.app.set_text_size))
        grid.addWidget(field("Theme", self.theme), 0, 0)
        grid.addWidget(field("Text size (Ctrl+Plus / Ctrl+Minus / Ctrl+0)", self.text_size), 0, 1)
        grid.addWidget(field("Week starts on", self._choice("week_start", combo(["sun", "mon"]))), 0, 2)
        grid.addWidget(field("Plan horizon (days)",
                             self._number("plan_horizon_days", spinbox(14, step=1, minimum=1, maximum=60))), 1, 0)
        grid.addWidget(field("End-of-day check-in (HH:MM)",
                             self._text("end_of_day_time", line_edit(placeholder="22:00"))), 1, 1)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.save_button = button("Save settings", self._save, kind="primary", tooltip="Save every setting above")
        row.addWidget(self.save_button)
        self.save_status = label("", "muted", wrap=True)
        row.addWidget(self.save_status, 1)
        layout.addLayout(row)
        return frame

    def _build_data(self) -> QFrame:
        frame, layout = card(margins=(14, 12, 14, 14), spacing=8)
        layout.addWidget(label("Data", "h2"))
        layout.addWidget(label(f"Everything lives in {config.default_db_path()}. Reset and Import ask for "
                               "confirmation in the coach panel.", "muted", wrap=True, selectable=True))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(button("Export data (JSON)", self._export, tooltip="Save all data to a JSON file"))
        row.addWidget(button("Import", self._import, tooltip="Replace all data with an exported JSON file"))
        self.reset_button = button("Reset to demo data", lambda: self.app.call("reset_demo_data", {}), tone="danger",
                                   tooltip="Replace all data with the demo data (asks for confirmation)")
        row.addWidget(self.reset_button)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        if self.loaded:
            return                      # do not overwrite what the student is typing
        values = self.app.read("get_settings")
        if not values:
            return
        for key, setter in self.setters.items():
            setter(str(values.get(key, "")))
        self.loaded = True
        self._key_hint()
        self.subtitle.setText(self.app.coach.status_text())

    def _status(self, widget: Any, text: str, tone: str = "") -> None:
        widget.setText(text)
        set_prop(widget, "tone", tone)

    def _key_hint(self) -> None:
        provider = self.provider.currentText()
        if provider == "offline":
            self.key_edit.setText("")
            self._status(self.key_status, "Offline mode needs no key.")
        elif self.app.coach.has_api_key(provider):
            self.key_edit.setText(KEY_PLACEHOLDER)
            self._status(self.key_status, "A key is saved for this provider.")
        else:
            self.key_edit.setText("")
            self._status(self.key_status, "No key saved for this provider yet.", "warning")

    # ------------------------------------------------------------ actions
    def values(self) -> dict[str, str]:
        return {key: getter() for key, getter in self.getters.items()}

    def _save(self) -> bool:
        result = self.app.call("update_settings", {"settings": self.values()})
        if not result.ok:
            self._status(self.save_status, result.summary, "danger")
            return False
        self._status(self.save_status, "Saved.", "success")
        stored = self.app.read("get_settings") or {}
        for key in config.TEXT_SETTINGS:            # show pasted values the way they were cleaned and stored
            if key in stored:
                self.setters[key](stored[key])
        self.app.settings_changed()
        self.subtitle.setText(self.app.coach.status_text())
        return True

    def _save_key(self) -> None:
        provider = self.provider.currentText()
        key = self.key_edit.text().strip()
        if key != KEY_PLACEHOLDER:
            key = config.clean_api_key(key)
        if provider == "offline":
            self._status(self.key_status, "Choose a provider first.", "warning")
            return
        if key == KEY_PLACEHOLDER:
            self._status(self.key_status, "The saved key is unchanged.")
            return
        if not self._save():
            return
        if self.app.coach.save_api_key(provider, key):
            self._status(self.key_status, "Key saved to the keyring." if key else "Key removed.", "success")
            self.key_edit.setText(KEY_PLACEHOLDER if key else "")
        else:
            env = "ANTHROPIC" if provider == "anthropic" else "OPENAI"
            self._status(self.key_status, f"The keyring is not available. Set the {env}_API_KEY environment "
                                          "variable instead.", "danger")
        self.subtitle.setText(self.app.coach.status_text())

    def _test(self) -> None:
        if not self._save():
            return
        self._status(self.key_status, "Testing…")
        self.test_button.setEnabled(False)

        def done(ok: bool, message: str) -> None:            # called on the worker thread
            def show() -> None:
                self.test_button.setEnabled(True)
                self._status(self.key_status, message, "success" if ok else "danger")
            self.app.post_ui(show)

        self.app.coach.test_connection_async(done)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export NeuroPlan data", "neuroplan-export.json", "JSON (*.json)")
        if path:
            self.export_to(path)

    def export_to(self, path: str) -> bool:
        data = self.app.read("export_data")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
        except OSError as exc:
            self.app.toast(f"Export failed: {exc}", error=True)
            return False
        self.app.toast(f"Exported to {path}")
        return True

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import NeuroPlan data", "", "JSON (*.json)")
        if path:
            self.import_from(path)

    def import_from(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            self.app.toast(f"Import failed: {exc}", error=True)
            return
        if not isinstance(data, dict):
            self.app.toast("Import failed: not a NeuroPlan export.", error=True)
            return
        self.app.call("import_data", {"data": data})
