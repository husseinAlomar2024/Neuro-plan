"""Where the data lives: project folder from source, %LOCALAPPDATA%\\NeuroPlan for the built exe.
Everything here runs inside tmp_path; the real LOCALAPPDATA is never touched."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from neuroplan import config
from neuroplan.db.repository import Repository

PROJECT = Path(__file__).resolve().parent.parent


def make_db(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    repo = Repository(path, fast=True)
    repo.insert_subject(name, "HEAVY", 3, 3, 50, 0, "#4F6FE0", True)
    repo.close()


def subject_names(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT name FROM subjects ORDER BY id")]
    finally:
        conn.close()


# ---------------------------------------------------------------- resolve_data_dir (pure)
def test_source_run_keeps_the_project_folder(tmp_path: Path) -> None:
    project = tmp_path / "project"
    assert config.resolve_data_dir(False, project, str(tmp_path / "Local"), tmp_path / "home") == project
    assert config.resolve_data_dir(False, project, None, tmp_path / "home") == project


def test_frozen_run_uses_localappdata(tmp_path: Path) -> None:
    exe_dir = tmp_path / "anywhere" / "dist"
    local = tmp_path / "Users" / "me" / "AppData" / "Local"
    assert config.resolve_data_dir(True, exe_dir, str(local), tmp_path / "home") == local / "NeuroPlan"


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_frozen_run_without_localappdata_falls_back_to_the_profile(tmp_path: Path, missing: str | None) -> None:
    home = tmp_path / "Users" / "me"
    expected = home / "AppData" / "Local" / "NeuroPlan"
    assert config.resolve_data_dir(True, tmp_path / "dist", missing, home) == expected


# ---------------------------------------------------------------- app_dir / default_db_path
def test_from_source_the_database_stays_in_the_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert not config.is_frozen()
    assert config.app_dir() == PROJECT
    assert config.default_db_path() == PROJECT / "neuroplan.db"
    assert not (tmp_path / "Local").exists(), "a source run must not create the per-user folder"


def test_frozen_app_dir_is_created_under_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local = tmp_path / "Local"
    exe = tmp_path / "dist" / "NeuroPlan.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert config.is_frozen() and config.install_dir() == exe.parent
    assert not (local / "NeuroPlan").exists()
    assert config.app_dir() == local / "NeuroPlan"
    assert (local / "NeuroPlan").is_dir(), "the folder is created when it is missing"
    assert config.app_dir() == local / "NeuroPlan", "and an existing folder is fine"
    assert config.default_db_path() == local / "NeuroPlan" / "neuroplan.db"
    assert config.app_dir() / config.LOG_FILENAME == local / "NeuroPlan" / "neuroplan.log"


def test_installed_exe_keeps_data_beside_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """build_exe.bat installs to %LOCALAPPDATA%\\NeuroPlan: program folder and data folder are the same."""
    local = tmp_path / "Local"
    make_db(local / "NeuroPlan" / "neuroplan.db", "Mine")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(local / "NeuroPlan" / "NeuroPlan.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert config.app_dir() == config.install_dir()
    assert config.migrate_legacy_db(config.install_dir(), config.app_dir()) is None
    assert subject_names(config.default_db_path()) == ["Mine"]


# ---------------------------------------------------------------- migrate_legacy_db
def test_old_database_next_to_the_exe_is_copied_on_first_run(tmp_path: Path) -> None:
    exe_dir, data_dir = tmp_path / "dist", tmp_path / "Local" / "NeuroPlan"
    make_db(exe_dir / "neuroplan.db", "Old Subject")
    original = (exe_dir / "neuroplan.db").read_bytes()
    assert not data_dir.exists()
    copied = config.migrate_legacy_db(exe_dir, data_dir)
    assert copied == data_dir / "neuroplan.db"
    assert subject_names(copied) == ["Old Subject"]
    assert (exe_dir / "neuroplan.db").read_bytes() == original, "the old file is copied, not moved"
    assert [p.name for p in data_dir.iterdir()] == ["neuroplan.db"], "no temporary file is left behind"
    # second run: the new database is in use and must not be replaced by the old one again
    repo = Repository(copied, fast=True)
    repo.insert_subject("Added Later", "CODING", 3, 3, 50, 0, "#4F6FE0", True)
    repo.close()
    assert config.migrate_legacy_db(exe_dir, data_dir) is None
    assert subject_names(copied) == ["Old Subject", "Added Later"]


def test_existing_database_is_never_overwritten(tmp_path: Path) -> None:
    exe_dir, data_dir = tmp_path / "dist", tmp_path / "Local" / "NeuroPlan"
    make_db(exe_dir / "neuroplan.db", "Old Subject")
    make_db(data_dir / "neuroplan.db", "Current Subject")
    assert config.migrate_legacy_db(exe_dir, data_dir) is None
    assert subject_names(data_dir / "neuroplan.db") == ["Current Subject"]


def test_nothing_to_migrate(tmp_path: Path) -> None:
    exe_dir, data_dir = tmp_path / "dist", tmp_path / "Local" / "NeuroPlan"
    exe_dir.mkdir()
    assert config.migrate_legacy_db(exe_dir, data_dir) is None
    assert not data_dir.exists(), "no database next to the exe: nothing is created"


def test_sqlite_side_files_travel_with_the_database(tmp_path: Path) -> None:
    exe_dir, data_dir = tmp_path / "dist", tmp_path / "Local" / "NeuroPlan"
    make_db(exe_dir / "neuroplan.db", "Old Subject")
    (exe_dir / "neuroplan.db-wal").write_bytes(b"wal")
    assert config.migrate_legacy_db(exe_dir, data_dir) is not None
    assert (data_dir / "neuroplan.db-wal").read_bytes() == b"wal"


# ---------------------------------------------------------------- icon
def test_icon_file_is_a_real_multi_size_ico() -> None:
    import struct
    assert config.icon_path() == PROJECT / "assets" / "neuroplan.ico"
    data = config.icon_path().read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count >= 4
    sizes = set()
    for i in range(count):
        width, _h, _c, _r, _planes, bits, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert bits == 32 and offset + length <= len(data)
        sizes.add(width or 256)
    assert {16, 32, 48, 256} <= sizes
