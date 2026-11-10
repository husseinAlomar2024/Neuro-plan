"""Shared fixtures. Every test uses a temporary database and a fixed 'today'."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuroplan.db.repository import Repository  # noqa: E402
from neuroplan.seed import seed_demo  # noqa: E402

TODAY = date(2026, 11, 10)  # a Tuesday: 180-minute evening session in the seed availability


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository(tmp_path / "test.db")
    yield r
    r.close()


@pytest.fixture
def seeded(repo: Repository) -> Repository:
    seed_demo(repo, TODAY)
    return repo
