"""Subjects: table with backlog bars, edit form, and the assessments of the selected subject."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

import customtkinter as ctk

from ... import config
from .. import theme
from ..widgets.common import Chip, clear, entry, field, option_menu
from . import BaseView

if TYPE_CHECKING:
    from ..app import NeuroPlanApp


class SubjectsView(BaseView):
    title = "Subjects"

    def __init__(self, master: object, app: "NeuroPlanApp") -> None:
        super().__init__(master, app)
        self._signature = ""
        self.subjects: list[dict[str, Any]] = []
        self.selected: Optional[str] = None          # subject name, None = new subject
        self.assessment_id: Optional[int] = None
        self.row_buttons: dict[tuple[str, str], ctk.CTkButton] = {}     # (subject, "archive"|"restore"|"delete")
        bar = self.header("Click a subject to edit it")
        self.show_archived = ctk.BooleanVar(value=False)
        self.archived_switch = ctk.CTkSwitch(bar, text="Show archived", variable=self.show_archived,
                                             font=theme.font(12), text_color=theme.TEXT,
                                             progress_color=theme.ACCENT, command=self._toggle_archived)
        self.archived_switch.pack(side="right")

        columns = ctk.CTkFrame(self, fg_color="transparent")
        columns.pack(fill="both", expand=True, padx=theme.PAD - 2, pady=(0, theme.PAD))
        columns.grid_columnconfigure(0, weight=5)
        columns.grid_columnconfigure(1, weight=4)
        columns.grid_rowconfigure(0, weight=1)
        self.table = ctk.CTkScrollableFrame(columns, fg_color="transparent")
        self.table.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ctk.CTkScrollableFrame(columns, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")

        # ---- edit form
        form = theme.card(right)
        form.pack(fill="x", padx=4, pady=4)
        self.form_title = theme.label(form, "New subject", 14, "bold")
        self.form_title.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)
        self.v = {name: ctk.StringVar() for name in
                  ("name", "category", "difficulty", "mastery", "minutes_per_unit", "total_units",
                   "expected_units_by_now", "done_units", "color_hex")}
        self.active = ctk.BooleanVar(value=True)
        field(form, "Name", lambda m: entry(m, self.v["name"], 220), 1, 0, 2)
        field(form, "Category", lambda m: option_menu(m, self.v["category"], list(config.CATEGORIES), 130), 2, 0)
        field(form, "Minutes per unit", lambda m: entry(m, self.v["minutes_per_unit"], 110), 2, 1)
        field(form, "Difficulty (1–5)", lambda m: option_menu(m, self.v["difficulty"], list("12345"), 110), 3, 0)
        field(form, "Mastery (1–5)", lambda m: option_menu(m, self.v["mastery"], list("12345"), 110), 3, 1)
        field(form, "Total units", lambda m: entry(m, self.v["total_units"], 110), 4, 0)
        field(form, "Expected by now", lambda m: entry(m, self.v["expected_units_by_now"], 110), 4, 1)
        field(form, "Done units", lambda m: entry(m, self.v["done_units"], 110), 5, 0)
        field(form, "Colour (#RRGGBB)", lambda m: entry(m, self.v["color_hex"], 110), 5, 1)
        ctk.CTkSwitch(form, text="Active (included in the plan)", variable=self.active, font=theme.font(12),
                      text_color=theme.TEXT, progress_color=theme.ACCENT).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=12, pady=6)
        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 12))
        theme.primary_button(buttons, "Save", self._save, width=80).pack(side="left", padx=4)
        theme.ghost_button(buttons, "New", self._new, width=64).pack(side="left", padx=4)
        self.delete_button = theme.ghost_button(buttons, "Delete", self._delete, width=72, text_color=theme.DANGER)
        self.delete_button.pack(side="left", padx=4)

        # ---- assessments
        box = theme.card(right)
        box.pack(fill="x", padx=4, pady=(8, 4))
        self.assess_title = theme.label(box, "Assessments", 14, "bold")
        self.assess_title.pack(anchor="w", padx=12, pady=(10, 2))
        self.assess_list = ctk.CTkFrame(box, fg_color="transparent")
        self.assess_list.pack(fill="x", padx=8)
        add = ctk.CTkFrame(box, fg_color="transparent")
        add.pack(fill="x", padx=6, pady=(4, 4))
        add.grid_columnconfigure(0, weight=1)
        add.grid_columnconfigure(1, weight=1)
        add.grid_columnconfigure(2, weight=1)
        self.a = {name: ctk.StringVar() for name in ("kind", "date", "title", "weight_pct", "unit_from", "unit_to")}
        self.a["kind"].set("midterm")
        field(add, "Kind", lambda m: option_menu(m, self.a["kind"], list(config.ASSESSMENT_KINDS), 110), 0, 0)
        field(add, "Date (YYYY-MM-DD)", lambda m: entry(m, self.a["date"], 110), 0, 1)
        field(add, "Weight %", lambda m: entry(m, self.a["weight_pct"], 60), 0, 2)
        field(add, "Title", lambda m: entry(m, self.a["title"], 110), 1, 0)
        field(add, "From unit", lambda m: entry(m, self.a["unit_from"], 60), 1, 1)
        field(add, "To unit", lambda m: entry(m, self.a["unit_to"], 60), 1, 2)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(2, 12))
        self.assess_save = theme.primary_button(row, "Add assessment", self._save_assessment, width=140)
        self.assess_save.pack(side="left", padx=4)
        theme.ghost_button(row, "Clear", self._clear_assessment, width=60).pack(side="left", padx=4)
        self._new()

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        data = self.app.read("list_subjects")
        self.subjects = data.get("subjects", [])
        archived = sum(1 for s in self.subjects if not s["active"])
        self.archived_switch.configure(text=f"Show archived ({archived})" if archived else "Show archived")
        signature = json.dumps(self.subjects, sort_keys=True) + str(self.selected) + str(self.show_archived.get())
        if signature == self._signature:
            return
        self._signature = signature
        clear(self.table)
        self.row_buttons = {}
        visible = self.visible_subjects()
        if not self.subjects:
            theme.label(self.table, "No subjects yet. Add one on the right, or tell the coach "
                                    "\"I have 3 subjects: …\".", 13, color=theme.MUTED, wraplength=380,
                        justify="left").pack(anchor="w", padx=10, pady=20)
        elif not visible:
            theme.label(self.table, "Every subject is archived. Turn on \"Show archived\" to restore one.", 13,
                        color=theme.MUTED, wraplength=380, justify="left").pack(anchor="w", padx=10, pady=20)
        for s in visible:
            self._row(s)
        if self.selected and not any(s["name"] == self.selected for s in visible):
            self._new()
        elif self.selected:
            self._load(self.selected)

    def visible_subjects(self) -> list[dict[str, Any]]:
        """Active subjects, plus the archived ones while "Show archived" is on."""
        return [s for s in self.subjects if s["active"] or self.show_archived.get()]

    def _toggle_archived(self) -> None:
        self._signature = ""
        self.refresh()

    def _row(self, s: dict[str, Any]) -> None:
        selected = s["name"] == self.selected
        row = theme.card(self.table, border_color=theme.ACCENT if selected else theme.BORDER)
        row.pack(fill="x", padx=4, pady=4)
        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))
        Chip(top, s["name"] + ("" if s["active"] else " (archived)"), s["color_hex"], 14).pack(side="left")
        theme.label(top, f"{s['category']} · difficulty {s['difficulty']} · mastery {s['mastery']}", 11,
                    color=theme.MUTED).pack(side="right")
        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.pack(fill="x", padx=12, pady=(0, 2))
        theme.label(mid, f"{s['done_units']} / {s['total_units']} units done · expected {s['expected_units_by_now']} · "
                         f"{s['minutes_per_unit']} min/unit", 12).pack(side="left")
        bar_row = ctk.CTkFrame(row, fg_color="transparent")
        bar_row.pack(fill="x", padx=12, pady=(2, 10))
        backlog = s["backlog_units"]
        color = theme.backlog_color(backlog)
        bar = ctk.CTkProgressBar(bar_row, height=8, progress_color=color, fg_color=theme.SURFACE_3)
        bar.pack(side="left", fill="x", expand=True)
        bar.set(1.0 if backlog == 0 else min(1.0, backlog / max(1, s["expected_units_by_now"])))
        theme.label(bar_row, "on track" if backlog == 0 else f"{backlog} behind", 11, "bold", color).pack(
            side="left", padx=(10, 0))
        upcoming = [a for a in s["assessments"] if a["days_until"] >= 0]
        if upcoming:
            a = upcoming[0]
            theme.label(row, f"Next: {a['title']} in {a['days_until']} day(s)", 11, color=theme.MUTED).pack(
                anchor="w", padx=12, pady=(0, 2))
        foot = ctk.CTkFrame(row, fg_color="transparent")      # own line: the row is too narrow to share one
        foot.pack(fill="x", padx=12, pady=(0, 8))
        name = s["name"]
        delete = theme.ghost_button(foot, "Delete", lambda: self.delete_subject(name), width=58, height=24,
                                    text_color=theme.DANGER)
        delete.pack(side="right", padx=(4, 0))
        if s["active"]:
            toggle = theme.ghost_button(foot, "Archive", lambda: self.set_archived(name, True), width=62, height=24)
        else:
            toggle = theme.ghost_button(foot, "Restore", lambda: self.set_archived(name, False), width=62,
                                        height=24, text_color=theme.ACCENT)
        toggle.pack(side="right")
        self.row_buttons[(name, "delete")] = delete
        self.row_buttons[(name, "archive" if s["active"] else "restore")] = toggle
        for widget in (row, top, mid, bar_row, foot):
            widget.bind("<Button-1>", lambda _e, name=s["name"]: self._select(name))
        for child in list(top.winfo_children()) + list(mid.winfo_children()):
            child.bind("<Button-1>", lambda _e, name=s["name"]: self._select(name))

    def _select(self, name: str) -> None:
        self.selected = name
        self._load(name)
        self._signature = ""
        self.refresh()

    def _load(self, name: str) -> None:
        s = next((x for x in self.subjects if x["name"] == name), None)
        if s is None:
            return
        self.form_title.configure(text=f"Edit {s['name']}")
        for key in self.v:
            self.v[key].set(str(s[key]))
        self.active.set(bool(s["active"]))
        self.delete_button.configure(state="normal")
        self.assess_title.configure(text=f"Assessments — {s['name']}")
        clear(self.assess_list)
        if not s["assessments"]:
            theme.label(self.assess_list, "None yet.", 12, color=theme.MUTED).pack(anchor="w", padx=6, pady=4)
        for a in s["assessments"]:
            line = ctk.CTkFrame(self.assess_list, fg_color=theme.SURFACE_2, corner_radius=8)
            line.pack(fill="x", pady=2)
            text = (f"{a['kind']} · {a['date']} · units {a['unit_from']}–{a['unit_to']} · {a['weight_pct']}%\n"
                    f"{a['title']}")
            item = theme.label(line, text, 11, justify="left", anchor="w")
            item.pack(side="left", padx=8, pady=4)
            theme.ghost_button(line, "Delete", lambda i=a["id"]: self.app.call("delete_assessment", {"id": i}),
                               width=56, height=24, text_color=theme.DANGER).pack(side="right", padx=4)
            theme.ghost_button(line, "Edit", lambda x=a: self._edit_assessment(x), width=44, height=24
                               ).pack(side="right")

    def _new(self) -> None:
        self.selected = None
        self.form_title.configure(text="New subject")
        defaults = {"name": "", "category": config.DEFAULT_SUBJECT["category"],
                    "difficulty": str(config.DEFAULT_SUBJECT["difficulty"]),
                    "mastery": str(config.DEFAULT_SUBJECT["mastery"]),
                    "minutes_per_unit": str(config.DEFAULT_SUBJECT["minutes_per_unit"]),
                    "total_units": "10", "expected_units_by_now": "0", "done_units": "0", "color_hex": ""}
        for key, value in defaults.items():
            self.v[key].set(str(value))
        self.active.set(True)
        self.delete_button.configure(state="disabled")
        self.assess_title.configure(text="Assessments")
        clear(self.assess_list)
        theme.label(self.assess_list, "Save or select a subject first.", 12, color=theme.MUTED).pack(
            anchor="w", padx=6, pady=4)
        self._clear_assessment()
        self._signature = ""

    # ------------------------------------------------------------ actions
    def _save(self) -> None:
        args: dict[str, Any] = {"name": self.selected or self.v["name"].get().strip(),
                                "category": self.v["category"].get(), "active": bool(self.active.get())}
        if self.selected and self.v["name"].get().strip() != self.selected:
            args["new_name"] = self.v["name"].get().strip()
        for key in ("difficulty", "mastery", "minutes_per_unit", "total_units", "expected_units_by_now",
                    "done_units"):
            args[key] = self.v[key].get().strip()
        color = self.v["color_hex"].get().strip()
        if color:
            args["color_hex"] = color
        result = self.app.call("upsert_subject", args)
        if result.ok and isinstance(result.data, dict):
            self.selected = result.data.get("name", self.selected)
            self._signature = ""
            self.refresh()

    def _delete(self) -> None:
        if self.selected:
            self.delete_subject(self.selected)

    def delete_subject(self, name: str) -> None:
        """Ask for the deletion; the Confirm/Cancel card appears in the coach panel first."""
        self.app.call("delete_subject", {"subject": name})

    def set_archived(self, name: str, archived: bool) -> None:
        """Archive (active=0: out of the plan, history kept) or restore a subject; the plan is rebuilt."""
        self.app.call("upsert_subject", {"name": name, "active": not archived})

    def _edit_assessment(self, a: dict[str, Any]) -> None:
        self.assessment_id = a["id"]
        for key in self.a:
            self.a[key].set(str(a[key]))
        self.assess_save.configure(text="Update assessment")

    def _clear_assessment(self) -> None:
        self.assessment_id = None
        for key in ("date", "title", "weight_pct", "unit_from", "unit_to"):
            self.a[key].set("")
        self.assess_save.configure(text="Add assessment")

    def _save_assessment(self) -> None:
        if not self.selected:
            self.app.toast("Select a subject first.", error=True)
            return
        args: dict[str, Any] = {"kind": self.a["kind"].get(), "date": self.a["date"].get().strip()}
        for key in ("title", "weight_pct", "unit_from", "unit_to"):
            value = self.a[key].get().strip()
            if value:
                args[key] = value
        if self.assessment_id is None:
            result = self.app.call("add_assessment", {"subject": self.selected, **args})
        else:
            result = self.app.call("update_assessment", {"id": self.assessment_id, **args})
        if result.ok:
            self._clear_assessment()
