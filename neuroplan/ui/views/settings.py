"""Settings: provider and models, API key (keyring), proactivity, appearance, data tools."""
from __future__ import annotations

import json
from tkinter import filedialog
from typing import TYPE_CHECKING

import customtkinter as ctk

from ... import config
from .. import theme
from ..widgets.common import entry, field, option_menu
from . import BaseView

if TYPE_CHECKING:
    from ..app import NeuroPlanApp

_KEY_PLACEHOLDER = "•••••••• (saved)"


class SettingsView(BaseView):
    title = "Settings"

    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, app)
        self._loaded = False
        self.header()
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=theme.PAD - 2, pady=(0, theme.PAD))
        self.v = {key: ctk.StringVar() for key in config.EDITABLE_SETTINGS}
        self.key_var = ctk.StringVar()

        # ---- coach / provider
        coach = theme.card(body)
        coach.pack(fill="x", padx=4, pady=4)
        theme.label(coach, "Coach", 14, "bold").grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 2))
        for col in range(3):
            coach.grid_columnconfigure(col, weight=1)
        field(coach, "Provider", lambda m: option_menu(m, self.v["provider"], list(config.PROVIDERS), 160,
                                                       lambda _v: self._key_hint()), 1, 0)
        field(coach, "Chat model", lambda m: entry(m, self.v["chat_model"], 180), 1, 1)
        field(coach, "Background model", lambda m: entry(m, self.v["background_model"], 180), 1, 2)
        field(coach, "Base URL (OpenAI-compatible, e.g. http://localhost:11434/v1)",
              lambda m: entry(m, self.v["base_url"], 380), 2, 0, 2)
        field(coach, "Proactive level", lambda m: option_menu(m, self.v["proactive_level"],
                                                              list(config.PROACTIVE_LEVELS), 120), 2, 2)
        field(coach, "API key (stored in the OS keyring, never in the database)",
              lambda m: entry(m, self.key_var, 380, show="•"), 3, 0, 2)
        field(coach, "Daily AI call cap", lambda m: entry(m, self.v["daily_llm_call_cap"], 90), 3, 2)
        row = ctk.CTkFrame(coach, fg_color="transparent")
        row.grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 12))
        theme.ghost_button(row, "Save key", self._save_key, width=84).pack(side="left", padx=4)
        self.test_button = theme.ghost_button(row, "Test connection", self._test, width=124)
        self.test_button.pack(side="left", padx=4)
        self.key_status = theme.label(row, "", 12, color=theme.MUTED, wraplength=420, justify="left")
        self.key_status.pack(side="left", padx=10)

        # ---- app
        general = theme.card(body)
        general.pack(fill="x", padx=4, pady=(8, 4))
        theme.label(general, "Planner", 14, "bold").grid(row=0, column=0, columnspan=4, sticky="w", padx=12,
                                                         pady=(10, 2))
        for col in range(4):
            general.grid_columnconfigure(col, weight=1)
        field(general, "Theme", lambda m: option_menu(m, self.v["theme"], ["Dark", "Light"], 110), 1, 0)
        field(general, "Week starts on", lambda m: option_menu(m, self.v["week_start"], ["sun", "mon"], 110), 1, 1)
        field(general, "Plan horizon (days)", lambda m: entry(m, self.v["plan_horizon_days"], 90), 1, 2)
        field(general, "End-of-day check-in (HH:MM)", lambda m: entry(m, self.v["end_of_day_time"], 90), 1, 3)
        save_row = ctk.CTkFrame(general, fg_color="transparent")
        save_row.grid(row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(4, 12))
        theme.primary_button(save_row, "Save settings", self._save, width=130).pack(side="left", padx=4)
        self.save_status = theme.label(save_row, "", 12, color=theme.MUTED)
        self.save_status.pack(side="left", padx=10)

        # ---- data
        data = theme.card(body)
        data.pack(fill="x", padx=4, pady=(8, 4))
        theme.label(data, "Data", 14, "bold").pack(anchor="w", padx=12, pady=(10, 2))
        theme.label(data, f"Everything lives in {config.default_db_path()}. Reset and Import ask for "
                          "confirmation in the coach panel.", 12, color=theme.MUTED, wraplength=640,
                    justify="left").pack(anchor="w", padx=12)
        buttons = ctk.CTkFrame(data, fg_color="transparent")
        buttons.pack(anchor="w", padx=8, pady=(6, 12))
        theme.ghost_button(buttons, "Export data (JSON)", self._export, width=150).pack(side="left", padx=4)
        theme.ghost_button(buttons, "Import", self._import, width=80).pack(side="left", padx=4)
        theme.ghost_button(buttons, "Reset to demo data", lambda: app.call("reset_demo_data", {}), width=150,
                           text_color=theme.DANGER).pack(side="left", padx=4)

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        if self._loaded:
            return                      # do not overwrite what the student is typing
        values = self.app.read("get_settings")
        if not values:
            return
        for key, var in self.v.items():
            var.set(values.get(key, ""))
        self._loaded = True
        self._key_hint()
        self.subtitle.configure(text=self.app.coach.status_text())

    def reload(self) -> None:
        self._loaded = False
        self.refresh()

    def _key_hint(self) -> None:
        provider = self.v["provider"].get()
        if provider == "offline":
            self.key_var.set("")
            self.key_status.configure(text="Offline mode needs no key.", text_color=theme.MUTED)
        elif self.app.coach.has_api_key(provider):
            self.key_var.set(_KEY_PLACEHOLDER)
            self.key_status.configure(text="A key is saved for this provider.", text_color=theme.MUTED)
        else:
            self.key_var.set("")
            self.key_status.configure(text="No key saved for this provider yet.", text_color=theme.WARNING)

    # ------------------------------------------------------------ actions
    def _save(self) -> bool:
        result = self.app.call("update_settings", {"settings": {k: v.get().strip() for k, v in self.v.items()}})
        if not result.ok:
            self.save_status.configure(text=result.summary, text_color=theme.DANGER)
            return False
        self.save_status.configure(text="Saved.", text_color=theme.SUCCESS)
        stored = self.app.read("get_settings") or {}
        for key in config.TEXT_SETTINGS:            # show pasted values the way they were cleaned up and stored
            if key in stored:
                self.v[key].set(stored[key])
        self.app.settings_changed()
        self.subtitle.configure(text=self.app.coach.status_text())
        return True

    def _save_key(self) -> None:
        provider = self.v["provider"].get()
        key = self.key_var.get().strip()
        if key != _KEY_PLACEHOLDER:
            key = config.clean_api_key(key)
        if provider == "offline":
            self.key_status.configure(text="Choose a provider first.", text_color=theme.WARNING)
            return
        if key == _KEY_PLACEHOLDER:
            self.key_status.configure(text="The saved key is unchanged.", text_color=theme.MUTED)
            return
        if not self._save():
            return
        if self.app.coach.save_api_key(provider, key):
            self.key_status.configure(text="Key saved to the keyring." if key else "Key removed.",
                                      text_color=theme.SUCCESS)
            self.key_var.set(_KEY_PLACEHOLDER if key else "")
        else:
            self.key_status.configure(
                text=f"The keyring is not available. Set the {('ANTHROPIC' if provider == 'anthropic' else 'OPENAI')}"
                     f"_API_KEY environment variable instead.", text_color=theme.DANGER)
        self.subtitle.configure(text=self.app.coach.status_text())

    def _test(self) -> None:
        if not self._save():
            return
        self.key_status.configure(text="Testing…", text_color=theme.MUTED)
        self.test_button.configure(state="disabled")

        def done(ok: bool, message: str) -> None:
            def show() -> None:
                self.test_button.configure(state="normal")
                self.key_status.configure(text=message, text_color=theme.SUCCESS if ok else theme.DANGER)
            self.app.post_ui(show)

        self.app.coach.test_connection_async(done)

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(title="Export NeuroPlan data", defaultextension=".json",
                                            initialfile="neuroplan-export.json",
                                            filetypes=[("JSON", "*.json")])
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
        path = filedialog.askopenfilename(title="Import NeuroPlan data", filetypes=[("JSON", "*.json")])
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
