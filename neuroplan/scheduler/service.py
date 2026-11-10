"""Bridge between the repository and the pure scheduler: build the State, regenerate the plan."""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from .. import config
from ..db.repository import Repository
from ..models import PlanResult, State
from .engine import generate_plan


def horizon_days(repo: Repository) -> int:
    try:
        return max(1, min(60, int(repo.get_setting("plan_horizon_days", "14") or 14)))
    except ValueError:
        return 14


def cap_boost_ids(repo: Repository) -> frozenset[int]:
    try:
        return frozenset(int(x) for x in json.loads(repo.get_setting("cap_boosts") or "[]"))
    except (ValueError, TypeError):
        return frozenset()


def build_state(repo: Repository, today: date) -> State:
    blocks = [b for b in repo.list_blocks(today)
              if b.status in ("done", "partial", "skipped") or (b.status == "pending" and b.locked)]
    return State(
        subjects=repo.list_subjects(),
        units=repo.all_units(),
        assessments=repo.list_assessments(),
        availability=repo.get_availability(),
        overrides=repo.get_overrides(today),
        reviews=repo.list_reviews(),
        blocks=blocks,
        last_studied=repo.last_studied(),
        cap_boost=cap_boost_ids(repo),
    )


def compute_plan(repo: Repository, today: date, days: Optional[int] = None) -> PlanResult:
    """Run the scheduler without touching the stored plan."""
    return generate_plan(build_state(repo, today), today, days or horizon_days(repo))


def regenerate(repo: Repository, today: date, days: Optional[int] = None) -> PlanResult:
    """SPEC 4.4: drop pending & unlocked blocks from today on, then store a fresh plan."""
    with repo.transaction():
        repo.delete_pending_unlocked_from(today)
        result = generate_plan(build_state(repo, today), today, days or horizon_days(repo))
        repo.insert_blocks(result.blocks)
    return result


__all__ = ["build_state", "compute_plan", "regenerate", "horizon_days", "cap_boost_ids", "config"]
