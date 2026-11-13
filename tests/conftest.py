"""Shared fixtures. Every test uses a temporary database and a fixed 'today'."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Qt tests never open real windows: the offscreen platform with one 1920x1080 screen (the default
# offscreen screen is 800x800, smaller than the app's minimum size). Qt splits platform parameters at
# ":", so the config path is given without its drive letter.
_SCREEN = os.path.splitdrive(str(Path(__file__).resolve().parent / "offscreen_screen.json"))[1].replace("\\", "/")
os.environ.setdefault("QT_QPA_PLATFORM", f"offscreen:configfile={_SCREEN}")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")   # the offscreen platform has no fonts of its own
os.environ["NEUROPLAN_NO_KEYRING"] = "1"      # tests never touch the real OS keyring
os.environ.pop("ANTHROPIC_API_KEY", None)     # ... and never call a real LLM API
os.environ.pop("OPENAI_API_KEY", None)

from neuroplan.db.repository import Repository  # noqa: E402
from neuroplan.seed import seed_demo  # noqa: E402

TODAY = date(2026, 11, 10)  # a Tuesday: 180-minute evening session in the seed availability


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "test.db", fast=True)
    yield r
    r.close()


@pytest.fixture
def seeded(repo: Repository) -> Repository:
    seed_demo(repo, TODAY)
    return repo
