"""P1: schema, repository, models, seed."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from neuroplan import config
from neuroplan.db.repository import Repository, open_database
from neuroplan.seed import review_stage_for_age, seed_demo, seed_if_first_launch

from .conftest import TODAY


def test_schema_created_with_defaults(repo: Repository) -> None:
    assert repo.get_setting("schema_version") == str(config.SCHEMA_VERSION)
    assert repo.get_setting("proactive_level") == "full"
    assert repo.get_setting("daily_llm_call_cap") == "60"
    assert repo.get_setting("plan_horizon_days") == "14"
    assert repo.list_subjects() == []
    assert len(repo.get_availability()) == 7


def test_subject_units_roundtrip(repo: Repository) -> None:
    sid = repo.insert_subject("Algebra", config.HEAVY, 4, 2, 60, 3, "#112233")
    repo.set_total_units(sid, 5)
    assert [u.number for u in repo.list_units(sid)] == [1, 2, 3, 4, 5]
    repo.set_total_units(sid, 3)
    assert len(repo.list_units(sid)) == 3
    subject = repo.get_subject_by_name("algebra")
    assert subject is not None and subject.difficulty == 4 and subject.active is True
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_subject("Algebra", config.HEAVY, 3, 3, 60, 0, "#000000")
    repo.delete_subject(sid)
    assert repo.list_units(sid) == []


def test_transaction_rolls_back(repo: Repository) -> None:
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.insert_subject("Temp", config.MEMORY, 3, 3, 30, 0, "#000000")
            raise RuntimeError("boom")
    assert repo.list_subjects() == []


def test_seed_matches_spec(seeded: Repository) -> None:
    subjects = {s.name: s for s in seeded.list_subjects()}
    assert set(subjects) == {"Deep Learning", "Machine Learning", "Internet Programming", "IELTS Prep"}
    expect = {"Deep Learning": (12, 7, 3, 75), "Machine Learning": (12, 6, 4, 60),
              "Internet Programming": (10, 5, 5, 50), "IELTS Prep": (30, 8, 6, 45)}
    for name, (units, expected, done, mpu) in expect.items():
        s = subjects[name]
        rows = seeded.list_units(s.id)
        assert len(rows) == units
        assert s.expected_units_by_now == expected
        assert sum(1 for u in rows if u.status == "done") == done
        assert s.minutes_per_unit == mpu
    dl = subjects["Deep Learning"]
    mid = seeded.list_assessments(dl.id)[0]
    assert (mid.kind, mid.date, mid.unit_from, mid.unit_to, mid.weight_pct) == (
        "midterm", TODAY + timedelta(days=10), 1, 6, 30)
    assert len(seeded.list_assessments()) == 5
    av = seeded.get_availability()
    assert av[6].minutes == 180 and av[6].start_time == "18:00" and av[6].peak_window == "night"
    assert av[4].minutes == 300 and av[5].start_time == "10:00" and av[5].peak_window == "morning"
    assert len(seeded.list_reviews()) == 18
    assert any(r.due_date == TODAY for r in seeded.list_reviews())
    assert seeded.list_blocks(TODAY), "the plan is generated after seeding"
    messages = seeded.list_messages()
    assert len(messages) == 1 and messages[0].role == "coach"


def test_seed_review_stage_matches_age() -> None:
    assert review_stage_for_age(1) == (0, 1)
    assert review_stage_for_age(2) == (1, 4)
    assert review_stage_for_age(4) == (1, 4)
    assert review_stage_for_age(6) == (2, 11)
    assert review_stage_for_age(12) == (3, 27)
    assert review_stage_for_age(100) is None


def test_seed_only_on_first_launch(repo: Repository) -> None:
    assert seed_if_first_launch(repo, TODAY) is True
    for s in repo.list_subjects():
        repo.delete_subject(s.id)
    assert seed_if_first_launch(repo, TODAY) is False
    assert repo.list_subjects() == []


def test_snapshot_restore_is_exact(seeded: Repository) -> None:
    before = seeded.snapshot()
    s = seeded.list_subjects()[0]
    seeded.update_subject(s.id, mastery=5, name="Renamed")
    seeded.delete_subject(seeded.list_subjects()[1].id)
    seeded.set_override(TODAY, 30, None)
    assert seeded.snapshot() != before
    seeded.restore(before)
    assert seeded.snapshot() == before


def test_export_import_roundtrip(seeded: Repository, tmp_path: Path) -> None:
    data = seeded.export_all()
    other = Repository(tmp_path / "other.db")
    other.import_all(data)
    assert other.snapshot() == seeded.snapshot()
    assert len(other.list_messages()) == 1
    other.close()


def test_empty_file_database_starts(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    r = open_database(path)
    assert r.list_subjects() == []
    r.close()


def test_corrupted_database_is_backed_up_and_recreated(tmp_path: Path) -> None:
    path = tmp_path / "bad.db"
    path.write_bytes(b"this is definitely not a sqlite database file" * 50)
    asked: list[Path] = []

    def ask(p: Path) -> bool:
        asked.append(p)
        return True

    r = open_database(path, ask)
    assert asked == [path]
    assert (tmp_path / "bad.db.bak").exists()
    assert r.list_subjects() == []
    r.close()


def test_corrupted_database_declined_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.db"
    path.write_bytes(b"garbage garbage garbage garbage garbage" * 50)
    with pytest.raises(sqlite3.DatabaseError):
        open_database(path, lambda p: False)
    assert path.exists()


def test_old_schema_database_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE subjects (id INTEGER PRIMARY KEY, name TEXT UNIQUE, category TEXT, difficulty INT, "
        "mastery INT, minutes_per_unit INT, expected_units_by_now INT);"
        "INSERT INTO subjects(name, category, difficulty, mastery, minutes_per_unit, expected_units_by_now) "
        "VALUES('Old Subject', 'HEAVY', 3, 3, 60, 2);"
        "CREATE TABLE study_blocks (id INTEGER PRIMARY KEY, plan_date TEXT, start_time TEXT, subject_id INT, "
        "unit_number INT, block_type TEXT, pomodoro TEXT, duration_min INT, task_text TEXT, status TEXT);")
    conn.commit()
    conn.close()
    r = open_database(path)
    subject = r.get_subject_by_name("Old Subject")
    assert subject is not None and subject.active is True and subject.color_hex.startswith("#")
    assert r.get_setting("schema_version") == str(config.SCHEMA_VERSION)
    sid = r.insert_subject("New", config.CODING, 3, 3, 50, 0, "#ABCDEF")
    r.set_total_units(sid, 2)
    assert len(r.list_units(sid)) == 2
    assert r.list_blocks(date(2000, 1, 1)) == []
    r.close()
