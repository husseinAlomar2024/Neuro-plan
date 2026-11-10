"""All SQL lives here. Every method returns dataclasses or plain values."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from .. import config
from ..models import (ActionLogEntry, Assessment, Availability, AvailabilityOverride, ChatMessage,
                      Review, StudyBlock, Subject, Unit)

SNAPSHOT_TABLES = ["subjects", "units", "assessments", "availability", "availability_override",
                   "study_blocks", "reviews"]
SNAPSHOT_SETTINGS = ["cap_boosts", "onboarding_skipped"]
ALL_TABLES = SNAPSHOT_TABLES + ["chat_messages", "action_log", "app_settings"]


def _d(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def schema_sql() -> str:
    return (config.resource_dir() / "db" / "schema.sql").read_text(encoding="utf-8")


class Repository:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA foreign_keys = ON")
            check = self._conn.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {check[0] if check else '?'}")
            self._migrate()
        except Exception:
            self._conn.close()
            raise

    # ------------------------------------------------------------ infrastructure
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Re-entrant transaction: the outermost block commits or rolls back everything."""
        with self._lock:
            if self._depth == 0:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("COMMIT")

    def _q(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _x(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return int(cur.lastrowid or 0)

    def _migrate(self) -> None:
        """Create missing tables, add columns missing from older schemas, stamp the version."""
        ref = sqlite3.connect(":memory:")
        ref.executescript(schema_sql())
        with self._lock:
            self._conn.executescript(schema_sql())
            for table in ALL_TABLES:
                have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                for col in ref.execute(f"PRAGMA table_info({table})"):
                    name, ctype, notnull, default = col[1], col[2], col[3], col[4]
                    if name in have:
                        continue
                    ddl = f"ALTER TABLE {table} ADD COLUMN {name} {ctype}"
                    if default is not None:
                        ddl += f" DEFAULT {default}"
                        if notnull:
                            ddl += " NOT NULL"
                    self._conn.execute(ddl)
        ref.close()
        with self.transaction():
            for key, value in config.DEFAULT_SETTINGS.items():
                if self.get_setting(key) is None:
                    self.set_setting(key, value)
            self.set_setting("schema_version", str(config.SCHEMA_VERSION))

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        rows = self._q("SELECT value FROM app_settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._x("INSERT INTO app_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._q("SELECT key, value FROM app_settings")}

    # ------------------------------------------------------------ subjects
    @staticmethod
    def _subject(r: sqlite3.Row) -> Subject:
        return Subject(r["id"], r["name"], r["category"], r["difficulty"], r["mastery"],
                       r["minutes_per_unit"], r["expected_units_by_now"], r["color_hex"],
                       bool(r["active"]), r["created_at"])

    def list_subjects(self, active_only: bool = False) -> list[Subject]:
        sql = "SELECT * FROM subjects" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        return [self._subject(r) for r in self._q(sql)]

    def get_subject(self, subject_id: int) -> Optional[Subject]:
        rows = self._q("SELECT * FROM subjects WHERE id = ?", (subject_id,))
        return self._subject(rows[0]) if rows else None

    def get_subject_by_name(self, name: str) -> Optional[Subject]:
        rows = self._q("SELECT * FROM subjects WHERE lower(name) = lower(?)", (name.strip(),))
        return self._subject(rows[0]) if rows else None

    def insert_subject(self, name: str, category: str, difficulty: int, mastery: int,
                       minutes_per_unit: int, expected_units_by_now: int, color_hex: str,
                       active: bool = True) -> int:
        return self._x(
            "INSERT INTO subjects(name, category, difficulty, mastery, minutes_per_unit, "
            "expected_units_by_now, color_hex, active, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (name.strip(), category, difficulty, mastery, minutes_per_unit, expected_units_by_now,
             color_hex, int(active), _now()))

    def update_subject(self, subject_id: int, **fields: Any) -> None:
        allowed = {"name", "category", "difficulty", "mastery", "minutes_per_unit",
                   "expected_units_by_now", "color_hex", "active"}
        self._update("subjects", "id", subject_id, allowed, fields)

    def delete_subject(self, subject_id: int) -> None:
        self._x("DELETE FROM subjects WHERE id = ?", (subject_id,))

    def _update(self, table: str, key_col: str, key: Any, allowed: set[str], fields: dict[str, Any]) -> None:
        cols = [c for c in fields if c in allowed]
        if not cols:
            return
        values: list[Any] = []
        for c in cols:
            v = fields[c]
            if isinstance(v, bool):
                v = int(v)
            elif isinstance(v, date):
                v = v.isoformat()
            values.append(v)
        sets = ", ".join(f"{c} = ?" for c in cols)
        self._x(f"UPDATE {table} SET {sets} WHERE {key_col} = ?", values + [key])

    # ------------------------------------------------------------ units
    @staticmethod
    def _unit(r: sqlite3.Row) -> Unit:
        return Unit(r["id"], r["subject_id"], r["number"], r["title"], r["minutes_logged"],
                    r["status"], _d(r["done_date"]))

    def list_units(self, subject_id: int) -> list[Unit]:
        return [self._unit(r) for r in
                self._q("SELECT * FROM units WHERE subject_id = ? ORDER BY number", (subject_id,))]

    def all_units(self) -> dict[int, list[Unit]]:
        out: dict[int, list[Unit]] = {}
        for r in self._q("SELECT * FROM units ORDER BY subject_id, number"):
            out.setdefault(r["subject_id"], []).append(self._unit(r))
        return out

    def set_total_units(self, subject_id: int, total: int) -> None:
        """Create units 1..total; remove units above total."""
        with self.transaction():
            have = {u.number for u in self.list_units(subject_id)}
            for n in range(1, total + 1):
                if n not in have:
                    self._x("INSERT INTO units(subject_id, number, title) VALUES(?,?,?)",
                            (subject_id, n, f"Unit {n}"))
            self._x("DELETE FROM units WHERE subject_id = ? AND number > ?", (subject_id, total))
            self._x("DELETE FROM reviews WHERE subject_id = ? AND unit_number > ?", (subject_id, total))

    def update_unit(self, unit_id: int, **fields: Any) -> None:
        self._update("units", "id", unit_id, {"title", "minutes_logged", "status", "done_date"}, fields)

    # ------------------------------------------------------------ assessments
    @staticmethod
    def _assessment(r: sqlite3.Row) -> Assessment:
        return Assessment(r["id"], r["subject_id"], r["kind"], r["title"], date.fromisoformat(r["date"]),
                          r["weight_pct"], r["unit_from"], r["unit_to"])

    def list_assessments(self, subject_id: Optional[int] = None) -> list[Assessment]:
        if subject_id is None:
            rows = self._q("SELECT * FROM assessments ORDER BY date, id")
        else:
            rows = self._q("SELECT * FROM assessments WHERE subject_id = ? ORDER BY date, id", (subject_id,))
        return [self._assessment(r) for r in rows]

    def get_assessment(self, assessment_id: int) -> Optional[Assessment]:
        rows = self._q("SELECT * FROM assessments WHERE id = ?", (assessment_id,))
        return self._assessment(rows[0]) if rows else None

    def insert_assessment(self, subject_id: int, kind: str, title: str, when: date, weight_pct: int,
                          unit_from: int, unit_to: int) -> int:
        return self._x("INSERT INTO assessments(subject_id, kind, title, date, weight_pct, unit_from, unit_to) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (subject_id, kind, title, when.isoformat(), weight_pct, unit_from, unit_to))

    def update_assessment(self, assessment_id: int, **fields: Any) -> None:
        self._update("assessments", "id", assessment_id,
                     {"kind", "title", "date", "weight_pct", "unit_from", "unit_to"}, fields)

    def delete_assessment(self, assessment_id: int) -> None:
        self._x("DELETE FROM assessments WHERE id = ?", (assessment_id,))

    # ------------------------------------------------------------ availability
    def get_availability(self) -> dict[int, Availability]:
        out = {r["weekday"]: Availability(r["weekday"], r["minutes"], r["start_time"], r["peak_window"])
               for r in self._q("SELECT * FROM availability")}
        for wd in range(7):
            out.setdefault(wd, Availability(wd, 0, config.DEFAULT_START_TIME, config.DEFAULT_PEAK))
        return out

    def set_availability(self, weekday: int, minutes: int, start_time: str, peak_window: str) -> None:
        self._x("INSERT INTO availability(weekday, minutes, start_time, peak_window) VALUES(?,?,?,?) "
                "ON CONFLICT(weekday) DO UPDATE SET minutes = excluded.minutes, "
                "start_time = excluded.start_time, peak_window = excluded.peak_window",
                (weekday, minutes, start_time, peak_window))

    def get_overrides(self, date_from: Optional[date] = None) -> dict[date, AvailabilityOverride]:
        rows = self._q("SELECT * FROM availability_override WHERE date >= ?",
                       ((date_from or date.min).isoformat(),))
        return {date.fromisoformat(r["date"]): AvailabilityOverride(date.fromisoformat(r["date"]),
                                                                   r["minutes"], r["start_time"])
                for r in rows}

    def set_override(self, when: date, minutes: int, start_time: Optional[str]) -> None:
        self._x("INSERT INTO availability_override(date, minutes, start_time) VALUES(?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET minutes = excluded.minutes, start_time = excluded.start_time",
                (when.isoformat(), minutes, start_time))

    def delete_override(self, when: date) -> None:
        self._x("DELETE FROM availability_override WHERE date = ?", (when.isoformat(),))

    # ------------------------------------------------------------ study blocks
    @staticmethod
    def _block(r: sqlite3.Row) -> StudyBlock:
        return StudyBlock(r["id"], date.fromisoformat(r["plan_date"]), r["start_time"], r["subject_id"],
                          r["unit_number"], r["block_type"], r["pomodoro"], r["duration_min"],
                          r["task_text"], r["status"], r["actual_min"], bool(r["locked"]),
                          r["order_index"], r["generated_at"])

    def list_blocks(self, date_from: date, date_to: Optional[date] = None) -> list[StudyBlock]:
        rows = self._q("SELECT * FROM study_blocks WHERE plan_date >= ? AND plan_date <= ? "
                       "ORDER BY plan_date, start_time, order_index, id",
                       (date_from.isoformat(), (date_to or date.max).isoformat()))
        return [self._block(r) for r in rows]

    def get_block(self, block_id: int) -> Optional[StudyBlock]:
        rows = self._q("SELECT * FROM study_blocks WHERE id = ?", (block_id,))
        return self._block(rows[0]) if rows else None

    def insert_block(self, b: StudyBlock) -> int:
        return self._x(
            "INSERT INTO study_blocks(plan_date, start_time, subject_id, unit_number, block_type, pomodoro, "
            "duration_min, task_text, status, actual_min, locked, order_index, generated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b.plan_date.isoformat(), b.start_time, b.subject_id, b.unit_number, b.block_type, b.pomodoro,
             b.duration_min, b.task_text, b.status, b.actual_min, int(b.locked), b.order_index,
             b.generated_at or _now()))

    def insert_blocks(self, blocks: list[StudyBlock]) -> None:
        with self.transaction():
            stamp = _now()
            for b in blocks:
                b.generated_at = b.generated_at or stamp
                b.id = self.insert_block(b)

    def update_block(self, block_id: int, **fields: Any) -> None:
        self._update("study_blocks", "id", block_id,
                     {"plan_date", "start_time", "status", "actual_min", "locked", "order_index",
                      "unit_number", "task_text"}, fields)

    def delete_pending_unlocked_from(self, start: date) -> None:
        self._x("DELETE FROM study_blocks WHERE status = 'pending' AND locked = 0 AND plan_date >= ?",
                (start.isoformat(),))

    def pending_blocks_before(self, day: date) -> list[StudyBlock]:
        rows = self._q("SELECT * FROM study_blocks WHERE status = 'pending' AND plan_date < ? "
                       "ORDER BY plan_date, order_index", (day.isoformat(),))
        return [self._block(r) for r in rows]

    def last_studied(self) -> dict[int, date]:
        rows = self._q("SELECT subject_id, MAX(plan_date) AS d FROM study_blocks "
                       "WHERE status IN ('done','partial') AND block_type != 'REVIEW' GROUP BY subject_id")
        return {r["subject_id"]: date.fromisoformat(r["d"]) for r in rows if r["d"]}

    def study_dates(self) -> set[date]:
        """Dates on which something was studied (for the streak)."""
        rows = self._q("SELECT DISTINCT plan_date AS d FROM study_blocks WHERE status IN ('done','partial') "
                       "UNION SELECT DISTINCT done_date AS d FROM units WHERE done_date IS NOT NULL")
        return {date.fromisoformat(r["d"]) for r in rows if r["d"]}

    # ------------------------------------------------------------ reviews
    @staticmethod
    def _review(r: sqlite3.Row) -> Review:
        return Review(r["id"], r["subject_id"], r["unit_number"], r["stage_index"],
                      date.fromisoformat(r["due_date"]), r["last_rating"], bool(r["done"]))

    def list_reviews(self, include_done: bool = False) -> list[Review]:
        sql = "SELECT * FROM reviews" + ("" if include_done else " WHERE done = 0")
        return [self._review(r) for r in self._q(sql + " ORDER BY due_date, subject_id, unit_number, id")]

    def get_review(self, review_id: int) -> Optional[Review]:
        rows = self._q("SELECT * FROM reviews WHERE id = ?", (review_id,))
        return self._review(rows[0]) if rows else None

    def insert_review(self, subject_id: int, unit_number: int, stage_index: int, due: date) -> int:
        return self._x("INSERT INTO reviews(subject_id, unit_number, stage_index, due_date) VALUES(?,?,?,?)",
                       (subject_id, unit_number, stage_index, due.isoformat()))

    def update_review(self, review_id: int, **fields: Any) -> None:
        self._update("reviews", "id", review_id, {"stage_index", "due_date", "last_rating", "done"}, fields)

    def delete_reviews_for_unit(self, subject_id: int, unit_number: int) -> None:
        self._x("DELETE FROM reviews WHERE subject_id = ? AND unit_number = ?", (subject_id, unit_number))

    # ------------------------------------------------------------ chat
    @staticmethod
    def _message(r: sqlite3.Row) -> ChatMessage:
        return ChatMessage(r["id"], r["ts"], r["role"], r["text"],
                           json.loads(r["actions_json"]) if r["actions_json"] else {}, bool(r["proactive"]))

    def add_message(self, role: str, text: str, actions: Optional[dict[str, Any]] = None,
                    proactive: bool = False) -> ChatMessage:
        ts = _now()
        mid = self._x("INSERT INTO chat_messages(ts, role, text, actions_json, proactive) VALUES(?,?,?,?,?)",
                      (ts, role, text, json.dumps(actions, ensure_ascii=False) if actions else None,
                       int(proactive)))
        return ChatMessage(mid, ts, role, text, actions or {}, proactive)

    def update_message_actions(self, message_id: int, actions: dict[str, Any]) -> None:
        self._x("UPDATE chat_messages SET actions_json = ? WHERE id = ?",
                (json.dumps(actions, ensure_ascii=False), message_id))

    def list_messages(self, limit: int = 200, after_id: int = 0) -> list[ChatMessage]:
        rows = self._q("SELECT * FROM (SELECT * FROM chat_messages WHERE id > ? ORDER BY id DESC LIMIT ?) "
                       "ORDER BY id", (after_id, limit))
        return [self._message(r) for r in rows]

    def count_messages(self, after_id: int = 0) -> int:
        return int(self._q("SELECT COUNT(*) AS n FROM chat_messages WHERE id > ?", (after_id,))[0]["n"])

    # ------------------------------------------------------------ action log / undo
    def add_action(self, source: str, tool: str, args: dict[str, Any],
                   inverse: Optional[dict[str, Any]]) -> int:
        aid = self._x("INSERT INTO action_log(ts, source, tool, args_json, inverse_json, undone) "
                      "VALUES(?,?,?,?,?,0)",
                      (_now(), source, tool, json.dumps(args, ensure_ascii=False, default=str),
                       json.dumps(inverse, ensure_ascii=False) if inverse is not None else None))
        # keep only the most recent snapshots so the log stays small
        self._x("UPDATE action_log SET inverse_json = NULL WHERE inverse_json IS NOT NULL AND id <= ?",
                (aid - config.UNDO_KEEP,))
        return aid

    @staticmethod
    def _action(r: sqlite3.Row) -> ActionLogEntry:
        return ActionLogEntry(r["id"], r["ts"], r["source"], r["tool"],
                              json.loads(r["args_json"]) if r["args_json"] else {},
                              json.loads(r["inverse_json"]) if r["inverse_json"] else None,
                              bool(r["undone"]))

    def last_undoable(self) -> Optional[ActionLogEntry]:
        rows = self._q("SELECT * FROM action_log WHERE undone = 0 AND inverse_json IS NOT NULL "
                       "ORDER BY id DESC LIMIT 1")
        return self._action(rows[0]) if rows else None

    def mark_undone(self, action_id: int) -> None:
        self._x("UPDATE action_log SET undone = 1 WHERE id = ?", (action_id,))

    def list_actions(self, limit: int = 50) -> list[ActionLogEntry]:
        return [self._action(r) for r in
                self._q("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------ snapshot / restore / export
    def _dump(self, table: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._q(f"SELECT * FROM {table}")]

    def _load(self, table: str, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            cols = list(row.keys())
            self._x(f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
                    [row[c] for c in cols])

    def snapshot(self) -> dict[str, Any]:
        """Planning state used for undo: every planning table plus a few planning settings."""
        snap: dict[str, Any] = {t: self._dump(t) for t in SNAPSHOT_TABLES}
        snap["settings"] = {k: self.get_setting(k) for k in SNAPSHOT_SETTINGS}
        return snap

    def restore(self, snap: dict[str, Any]) -> None:
        with self.transaction():
            for table in reversed(SNAPSHOT_TABLES):
                self._x(f"DELETE FROM {table}")
            for table in SNAPSHOT_TABLES:
                self._load(table, snap.get(table, []))
            for key, value in (snap.get("settings") or {}).items():
                if value is None:
                    self._x("DELETE FROM app_settings WHERE key = ?", (key,))
                else:
                    self.set_setting(key, value)

    def export_all(self) -> dict[str, Any]:
        data: dict[str, Any] = {t: self._dump(t) for t in SNAPSHOT_TABLES + ["chat_messages"]}
        data["app_settings"] = self.all_settings()
        data["format"] = "neuroplan-export-1"
        return data

    def import_all(self, data: dict[str, Any]) -> None:
        with self.transaction():
            self._x("DELETE FROM chat_messages")
            self._x("DELETE FROM action_log")
            self.restore({t: data.get(t, []) for t in SNAPSHOT_TABLES})
            self._load("chat_messages", data.get("chat_messages", []))
            for key, value in (data.get("app_settings") or {}).items():
                if value is not None:
                    self.set_setting(key, str(value))
            self.set_setting("schema_version", str(config.SCHEMA_VERSION))

    def clear_all(self) -> None:
        """Wipe planning data and chat (used by 'Reset to demo data'). Settings are kept."""
        with self.transaction():
            for table in reversed(SNAPSHOT_TABLES):
                self._x(f"DELETE FROM {table}")
            self._x("DELETE FROM chat_messages")
            self._x("DELETE FROM action_log")
            for key in SNAPSHOT_SETTINGS + ["last_briefing_date", "conversation_summary",
                                            "summarised_upto_id", "last_checkin_date",
                                            "last_reviews_nudge_date"]:
                self._x("DELETE FROM app_settings WHERE key = ?", (key,))


def open_database(path: str | Path,
                  on_corrupt: Optional[Callable[[Path], bool]] = None) -> Repository:
    """Open (or create) the database. A corrupted file is renamed to .bak and recreated,
    after asking through ``on_corrupt`` (which returns True to proceed)."""
    p = Path(path)
    try:
        return Repository(p)
    except sqlite3.DatabaseError:
        if on_corrupt is not None and not on_corrupt(p):
            raise
        backup = p.with_name(p.name + ".bak")
        n = 1
        while backup.exists():
            backup = p.with_name(f"{p.name}.bak{n}")
            n += 1
        p.rename(backup)
        return Repository(p)
