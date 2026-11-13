"""Subjects: cards with backlog bars and Archive/Restore/Delete, the edit form, and the assessments of the
selected subject. List and form sit in a splitter whose position is saved."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtCore import QByteArray, QDate, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDateEdit, QFrame, QGridLayout, QHBoxLayout, QProgressBar, QVBoxLayout,
                               QWidget)

from ... import config
from ..widgets import (Chip, Splitter, button, card, clear_layout, combo, field, label, line_edit, scroll_area,
                       spinbox)
from . import BaseView
from .today import backlog_tone

if TYPE_CHECKING:
    from ..app import MainWindow


class SubjectCard(QFrame):
    """One subject in the list; a click selects it."""

    def __init__(self, view: "SubjectsView", s: dict[str, Any], selected: bool) -> None:
        super().__init__()
        self.view = view
        self.name = s["name"]
        self.setProperty("card", "selected" if selected else "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Edit {s['name']}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)
        layout.addWidget(Chip(s["name"] + ("" if s["active"] else " (archived)"), s["color_hex"], 14))
        layout.addWidget(label(f"{s['category']} · difficulty {s['difficulty']} · mastery {s['mastery']}",
                               "small", wrap=True))
        layout.addWidget(label(f"{s['done_units']} / {s['total_units']} units done · expected "
                               f"{s['expected_units_by_now']} · {s['minutes_per_unit']} min/unit", wrap=True))
        bar_row = QHBoxLayout()
        bar_row.setSpacing(10)
        backlog = s["backlog_units"]
        tone = backlog_tone(backlog)
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0, 1000)
        bar.setValue(1000 if backlog == 0 else int(1000 * min(1.0, backlog / max(1, s["expected_units_by_now"]))))
        bar.setProperty("tone", tone)
        bar_row.addWidget(bar, 1)
        bar_row.addWidget(label("on track" if backlog == 0 else f"{backlog} behind", "strong", tone))
        layout.addLayout(bar_row)
        upcoming = [a for a in s["assessments"] if a["days_until"] >= 0]
        if upcoming:
            a = upcoming[0]
            layout.addWidget(label(f"Next: {a['title']} in {a['days_until']} day(s)", "small", wrap=True))
        foot = QHBoxLayout()
        foot.setSpacing(4)
        foot.addStretch(1)
        name = s["name"]
        if s["active"]:
            self.toggle = button("Archive", lambda: view.set_archived(name, True),
                                 tooltip="Take this subject out of the plan (history is kept)")
        else:
            self.toggle = button("Restore", lambda: view.set_archived(name, False), tone="accent",
                                 tooltip="Put this subject back into the plan")
        self.delete = button("Delete", lambda: view.delete_subject(name), tone="danger",
                             tooltip="Delete this subject (asks for confirmation in the coach panel)")
        foot.addWidget(self.toggle)
        foot.addWidget(self.delete)
        layout.addLayout(foot)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.view.select(self.name)
        super().mousePressEvent(event)


class SubjectsView(BaseView):
    title = "Subjects"

    def __init__(self, app: "MainWindow") -> None:
        super().__init__(app)
        self.subtitle.setText("Click a subject to edit it")
        self._signature = ""
        self.subjects: list[dict[str, Any]] = []
        self.selected: Optional[str] = None           # subject name, None = new subject
        self.assessment_id: Optional[int] = None
        self.cards: dict[str, SubjectCard] = {}
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.toggled.connect(self._toggle_archived)
        self.header_row.addWidget(self.show_archived)

        self.splitter = Splitter()
        list_body = QWidget()
        self.list_layout = QVBoxLayout(list_body)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.list_scroll = scroll_area(list_body)
        self.list_scroll.setMinimumWidth(config.COLUMN_MIN_WIDTH)
        self.splitter.addWidget(self.list_scroll)
        right_body = QWidget()
        right = QVBoxLayout(right_body)
        right.setContentsMargins(4, 0, 4, 0)
        right.setSpacing(10)
        right.addWidget(self._build_form())
        right.addWidget(self._build_assessments())
        right.addStretch(1)
        self.form_scroll = scroll_area(right_body)
        self.form_scroll.setMinimumWidth(config.COLUMN_MIN_WIDTH)
        self.splitter.addWidget(self.form_scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.root.addWidget(self.splitter, 1)
        self._save_split = QTimer(self, singleShot=True, interval=400, timeout=self._save_splitter)
        self.splitter.splitterMoved.connect(lambda *_: self._save_split.start())
        self.splitter.reset_requested.connect(self.reset_splitter)
        self._restored = False
        self.new_subject()

    # ------------------------------------------------------------ layout state
    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._restored:
            self._restored = True
            state = self.app.setting("splitter_subjects")
            if not (state and self.splitter.restoreState(QByteArray.fromBase64(state.encode()))):
                self.reset_splitter(save=False)

    def reset_splitter(self, save: bool = True) -> None:
        total = max(sum(self.splitter.sizes()), 2 * config.COLUMN_MIN_WIDTH)
        left = min(config.SUBJECTS_LIST_DEFAULT_WIDTH, total - config.COLUMN_MIN_WIDTH)
        self.splitter.setSizes([left, total - left])
        if save:
            self._save_splitter()

    def _save_splitter(self) -> None:
        self.app.save_prefs({"splitter_subjects": bytes(self.splitter.saveState().toBase64()).decode()})

    # ------------------------------------------------------------ form
    def _build_form(self) -> QFrame:
        frame, layout = card(margins=(14, 12, 14, 14), spacing=8)
        self.form_title = label("New subject", "h2")
        layout.addWidget(self.form_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.name_edit = line_edit(placeholder="e.g. Linear Algebra")
        self.category = combo(list(config.CATEGORIES))
        self.minutes_per_unit = spinbox(config.DEFAULT_SUBJECT["minutes_per_unit"], step=5, minimum=5, maximum=600)
        self.difficulty = combo(list("12345"))
        self.mastery = combo(list("12345"))
        self.total_units = spinbox(10, step=1, minimum=0, maximum=999)
        self.expected = spinbox(0, step=1, minimum=0, maximum=999)
        self.done_units = spinbox(0, step=1, minimum=0, maximum=999)
        self.color_edit = line_edit(placeholder="#RRGGBB (optional)")
        grid.addWidget(field("Name", self.name_edit), 0, 0, 1, 2)
        grid.addWidget(field("Category", self.category), 1, 0)
        grid.addWidget(field("Minutes per unit", self.minutes_per_unit), 1, 1)
        grid.addWidget(field("Difficulty (1–5)", self.difficulty), 2, 0)
        grid.addWidget(field("Mastery (1–5)", self.mastery), 2, 1)
        grid.addWidget(field("Total units", self.total_units), 3, 0)
        grid.addWidget(field("Expected by now", self.expected), 3, 1)
        grid.addWidget(field("Done units", self.done_units), 4, 0)
        grid.addWidget(field("Colour", self.color_edit), 4, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.active = QCheckBox("Active (included in the plan)")
        self.active.setChecked(True)
        layout.addWidget(self.active)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.save_button = button("Save", self.save, kind="primary", tooltip="Save the subject; the plan is rebuilt")
        self.new_button = button("New", self.new_subject, tooltip="Clear the form for a new subject")
        self.delete_button = button("Delete", self._delete_selected, tone="danger",
                                    tooltip="Delete this subject (asks for confirmation)")
        for widget in (self.save_button, self.new_button, self.delete_button):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _build_assessments(self) -> QFrame:
        frame, layout = card(margins=(14, 12, 14, 14), spacing=8)
        self.assess_title = label("Assessments", "h2")
        layout.addWidget(self.assess_title)
        self.assess_list = QVBoxLayout()
        self.assess_list.setSpacing(4)
        layout.addLayout(self.assess_list)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.kind = combo(list(config.ASSESSMENT_KINDS), "midterm")
        self.date = QDateEdit()
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setCalendarPopup(True)
        self.weight = spinbox(0, step=5, minimum=0, maximum=100, suffix=" %")
        self.weight.setSpecialValueText("—")
        self.assess_title_edit = line_edit(placeholder="optional")
        self.unit_from = spinbox(0, step=1, minimum=0, maximum=999)
        self.unit_from.setSpecialValueText("—")
        self.unit_to = spinbox(0, step=1, minimum=0, maximum=999)
        self.unit_to.setSpecialValueText("—")
        grid.addWidget(field("Kind", self.kind), 0, 0)
        grid.addWidget(field("Date", self.date), 0, 1)
        grid.addWidget(field("Weight", self.weight), 0, 2)
        grid.addWidget(field("Title", self.assess_title_edit), 1, 0)
        grid.addWidget(field("From unit", self.unit_from), 1, 1)
        grid.addWidget(field("To unit", self.unit_to), 1, 2)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.assess_save = button("Add assessment", self.save_assessment, kind="primary")
        row.addWidget(self.assess_save)
        row.addWidget(button("Clear", self.clear_assessment, tooltip="Clear the assessment fields"))
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        data = self.app.read("list_subjects")
        self.subjects = data.get("subjects", [])
        archived = sum(1 for s in self.subjects if not s["active"])
        self.show_archived.setText(f"Show archived ({archived})" if archived else "Show archived")
        signature = json.dumps(self.subjects, sort_keys=True) + str(self.selected) + str(self.show_archived.isChecked())
        if signature == self._signature:
            return
        self._signature = signature
        clear_layout(self.list_layout)
        self.cards = {}
        visible = self.visible_subjects()
        if not self.subjects:
            self.list_layout.addWidget(label("No subjects yet. Add one on the right, or tell the coach "
                                             "\"I have 3 subjects: …\".", "muted", wrap=True))
        elif not visible:
            self.list_layout.addWidget(label("Every subject is archived. Turn on \"Show archived\" to restore one.",
                                             "muted", wrap=True))
        for s in visible:
            subject_card = SubjectCard(self, s, s["name"] == self.selected)
            self.cards[s["name"]] = subject_card
            self.list_layout.addWidget(subject_card)
        self.list_layout.addStretch(1)
        if self.selected and not any(s["name"] == self.selected for s in visible):
            self.new_subject()
        elif self.selected:
            self._load(self.selected)

    def visible_subjects(self) -> list[dict[str, Any]]:
        """Active subjects, plus the archived ones while "Show archived" is on."""
        return [s for s in self.subjects if s["active"] or self.show_archived.isChecked()]

    def _toggle_archived(self, _checked: bool) -> None:
        self._signature = ""
        self.refresh()

    def select(self, name: str) -> None:
        self.selected = name
        self._signature = ""
        self.refresh()

    def _load(self, name: str) -> None:
        s = next((x for x in self.subjects if x["name"] == name), None)
        if s is None:
            return
        self.form_title.setText(f"Edit {s['name']}")
        self.name_edit.setText(s["name"])
        self.category.setCurrentText(s["category"])
        self.minutes_per_unit.setValue(int(s["minutes_per_unit"]))
        self.difficulty.setCurrentText(str(s["difficulty"]))
        self.mastery.setCurrentText(str(s["mastery"]))
        self.total_units.setValue(int(s["total_units"]))
        self.expected.setValue(int(s["expected_units_by_now"]))
        self.done_units.setValue(int(s["done_units"]))
        self.color_edit.setText(s["color_hex"])
        self.active.setChecked(bool(s["active"]))
        self.delete_button.setEnabled(True)
        self.assess_title.setText(f"Assessments — {s['name']}")
        clear_layout(self.assess_list)
        if not s["assessments"]:
            self.assess_list.addWidget(label("None yet.", "muted"))
        for a in s["assessments"]:
            row, row_layout = card("inset", vertical=False, margins=(8, 4, 4, 4), spacing=4)
            text = (f"{a['kind']} · {a['date']} · units {a['unit_from']}–{a['unit_to']} · {a['weight_pct']}%\n"
                    f"{a['title']}")
            row_layout.addWidget(label(text, wrap=True), 1)
            row_layout.addWidget(button("Edit", lambda x=a: self.edit_assessment(x),
                                        tooltip="Load this assessment into the fields below"))
            row_layout.addWidget(button("Delete", lambda i=a["id"]: self.app.call("delete_assessment", {"id": i}),
                                        tone="danger", tooltip="Delete this assessment"))
            self.assess_list.addWidget(row)

    def new_subject(self) -> None:
        self.selected = None
        self.form_title.setText("New subject")
        self.name_edit.setText("")
        self.category.setCurrentText(config.DEFAULT_SUBJECT["category"])
        self.minutes_per_unit.setValue(int(config.DEFAULT_SUBJECT["minutes_per_unit"]))
        self.difficulty.setCurrentText(str(config.DEFAULT_SUBJECT["difficulty"]))
        self.mastery.setCurrentText(str(config.DEFAULT_SUBJECT["mastery"]))
        self.total_units.setValue(10)
        self.expected.setValue(0)
        self.done_units.setValue(0)
        self.color_edit.setText("")
        self.active.setChecked(True)
        self.delete_button.setEnabled(False)
        self.assess_title.setText("Assessments")
        clear_layout(self.assess_list)
        self.assess_list.addWidget(label("Save or select a subject first.", "muted"))
        self.clear_assessment()
        self._signature = ""

    # ------------------------------------------------------------ actions
    def save(self) -> None:
        name = self.name_edit.text().strip()
        args: dict[str, Any] = {"name": self.selected or name, "category": self.category.currentText(),
                                "active": self.active.isChecked(),
                                "difficulty": int(self.difficulty.currentText()),
                                "mastery": int(self.mastery.currentText()),
                                "minutes_per_unit": self.minutes_per_unit.value(),
                                "total_units": self.total_units.value(),
                                "expected_units_by_now": self.expected.value(),
                                "done_units": self.done_units.value()}
        if self.selected and name != self.selected:
            args["new_name"] = name
        color = self.color_edit.text().strip()
        if color:
            args["color_hex"] = color
        result = self.app.call("upsert_subject", args)
        if result.ok and isinstance(result.data, dict):
            self.selected = result.data.get("name", self.selected)
            self._signature = ""
            self.refresh()

    def _delete_selected(self) -> None:
        if self.selected:
            self.delete_subject(self.selected)

    def delete_subject(self, name: str) -> None:
        """Ask for the deletion; the Confirm/Cancel card appears in the coach panel first."""
        self.app.call("delete_subject", {"subject": name})

    def set_archived(self, name: str, archived: bool) -> None:
        """Archive (out of the plan, history kept) or restore a subject; the plan is rebuilt."""
        self.app.call("upsert_subject", {"name": name, "active": not archived})

    def edit_assessment(self, a: dict[str, Any]) -> None:
        self.assessment_id = a["id"]
        self.kind.setCurrentText(a["kind"])
        self.date.setDate(QDate.fromString(a["date"], "yyyy-MM-dd"))
        self.assess_title_edit.setText(a["title"] or "")
        self.weight.setValue(int(a["weight_pct"] or 0))
        self.unit_from.setValue(int(a["unit_from"] or 0))
        self.unit_to.setValue(int(a["unit_to"] or 0))
        self.assess_save.setText("Update assessment")

    def clear_assessment(self) -> None:
        self.assessment_id = None
        today = self.app.today()
        self.date.setDate(QDate(today.year, today.month, today.day))
        self.assess_title_edit.setText("")
        self.weight.setValue(0)
        self.unit_from.setValue(0)
        self.unit_to.setValue(0)
        self.assess_save.setText("Add assessment")

    def save_assessment(self) -> None:
        if not self.selected:
            self.app.toast("Select a subject first.", error=True)
            return
        args: dict[str, Any] = {"kind": self.kind.currentText(), "date": self.date.date().toString("yyyy-MM-dd")}
        if self.assess_title_edit.text().strip():
            args["title"] = self.assess_title_edit.text().strip()
        for key, spin in (("weight_pct", self.weight), ("unit_from", self.unit_from), ("unit_to", self.unit_to)):
            if spin.value():
                args[key] = spin.value()
        if self.assessment_id is None:
            result = self.app.call("add_assessment", {"subject": self.selected, **args})
        else:
            result = self.app.call("update_assessment", {"id": self.assessment_id, **args})
        if result.ok:
            self.clear_assessment()
