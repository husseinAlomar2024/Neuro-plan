"""Bridge between the repository and the pure scheduler: build the State, regenerate the plan."""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from ..db.repository import Repository
from ..models import PlanResult, State, StudyBlock
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
    """SPEC 4.4: replace pending & unlocked blocks from today on with a fresh plan.

    Blocks that come out identical keep their row (and id), so a block the student is looking at
    or timing does not disappear just because something unrelated changed."""
    with repo.transaction():
        old = [b for b in repo.list_blocks(today) if b.status == "pending" and not b.locked]
        result = generate_plan(build_state(repo, today), today, days or horizon_days(repo))
        pool: dict[tuple[object, ...], list[StudyBlock]] = {}
        for b in old:
            pool.setdefault(_identity(b), []).append(b)
        fresh: list[StudyBlock] = []
        for b in result.blocks:
            same = pool.get(_identity(b))
            if same:
                kept = same.pop(0)
                b.id, b.generated_at = kept.id, kept.generated_at
                if (kept.start_time, kept.order_index) != (b.start_time, b.order_index):
                    repo.update_block(kept.id, start_time=b.start_time, order_index=b.order_index)
            else:
                fresh.append(b)
        for leftovers in pool.values():
            for b in leftovers:
                repo.delete_block(b.id)
        repo.insert_blocks(fresh)
    return result


def _identity(b: StudyBlock) -> tuple[object, ...]:
    return (b.plan_date, b.subject_id, b.block_type, b.unit_number, b.duration_min, b.pomodoro, b.task_text)

