"""Spaced repetition (SPEC 4.5)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from neuroplan import config
from neuroplan.db.repository import Repository
from neuroplan.tools.executor import ToolExecutor, next_review_state

from .conftest import TODAY


@pytest.fixture
def ex(seeded: Repository) -> ToolExecutor:
    return ToolExecutor(seeded, today_fn=lambda: TODAY)


def _dl(repo: Repository) -> int:
    s = repo.get_subject_by_name("Deep Learning")
    assert s is not None
    return s.id


def test_completion_creates_stage0_review_due_tomorrow(ex: ToolExecutor, seeded: Repository) -> None:
    dl = _dl(seeded)
    blocks = [b for b in seeded.list_blocks(TODAY, TODAY) if b.subject_id == dl and b.block_type == config.CATCHUP]
    assert blocks and blocks[0].unit_number == 4
    assert ex.execute("complete_block", {"id": blocks[0].id, "actual_min": 50}).ok
    unit4 = next(u for u in seeded.list_units(dl) if u.number == 4)
    assert (unit4.status, unit4.minutes_logged) == ("in_progress", 50)
    assert not [r for r in seeded.list_reviews() if r.subject_id == dl and r.unit_number == 4]
    # finish the unit: 25 more minutes needed, 50 logged -> 25 overflow into unit 5
    nxt = next(b for b in seeded.list_blocks(TODAY) if b.subject_id == dl and b.status == "pending"
               and b.block_type == config.CATCHUP)
    assert nxt.unit_number == 4
    assert ex.execute("complete_block", {"id": nxt.id}).ok
    units = {u.number: u for u in seeded.list_units(dl)}
    assert units[4].status == "done" and units[4].done_date == TODAY and units[4].minutes_logged == 75
    assert (units[5].status, units[5].minutes_logged) == ("in_progress", 25)
    review = next(r for r in seeded.list_reviews() if r.subject_id == dl and r.unit_number == 4)
    assert review.stage_index == 0 and review.due_date == TODAY + timedelta(days=1) and not review.done


@pytest.mark.parametrize("stage,rating,new_stage,offset", [
    (0, "good", 1, 3), (1, "good", 2, 7), (3, "good", 4, 35),
    (2, "again", 0, 1), (4, "again", 0, 1),
    (0, "hard", 0, 1), (1, "hard", 1, 2), (2, "hard", 2, 4), (3, "hard", 3, 8), (4, "hard", 4, 18),
    (0, "easy", 2, 7), (1, "easy", 3, 16), (2, "easy", 4, 35),
])
def test_rating_transitions(stage: int, rating: str, new_stage: int, offset: int) -> None:
    assert next_review_state(stage, rating, TODAY) == (new_stage, TODAY + timedelta(days=offset))


@pytest.mark.parametrize("stage,rating", [(4, "good"), (4, "easy"), (3, "easy")])
def test_after_last_stage_review_is_done(stage: int, rating: str) -> None:
    assert next_review_state(stage, rating, TODAY)[1] is None


def test_complete_review_tool_updates_row(ex: ToolExecutor, seeded: Repository) -> None:
    review = next(r for r in seeded.list_reviews() if r.due_date == TODAY)
    assert review.stage_index == 1
    assert ex.execute("complete_review", {"id": review.id, "rating": "good"}).ok
    after = seeded.get_review(review.id)
    assert after is not None
    assert (after.stage_index, after.due_date, after.last_rating, after.done) == (
        2, TODAY + timedelta(days=7), "good", False)
    assert ex.execute("complete_review", {"id": review.id, "rating": "again"}).ok
    after = seeded.get_review(review.id)
    assert after is not None and (after.stage_index, after.due_date) == (0, TODAY + timedelta(days=1))
    assert ex.execute("complete_review", {"id": review.id, "rating": "hard"}).ok
    after = seeded.get_review(review.id)
    assert after is not None and (after.stage_index, after.due_date) == (0, TODAY + timedelta(days=1))
    assert ex.execute("complete_review", {"id": review.id, "rating": "easy"}).ok
    after = seeded.get_review(review.id)
    assert after is not None and (after.stage_index, after.due_date) == (2, TODAY + timedelta(days=7))


def test_review_finishes_after_last_stage(ex: ToolExecutor, seeded: Repository) -> None:
    review = seeded.list_reviews()[0]
    seeded.update_review(review.id, stage_index=len(config.REVIEW_STAGES_DAYS) - 1)
    result = ex.execute("complete_review", {"id": review.id, "rating": "good"})
    assert result.ok and "complete" in result.summary
    after = seeded.get_review(review.id)
    assert after is not None and after.done
    assert review.id not in {r.id for r in seeded.list_reviews()}
    again = ex.execute("complete_review", {"id": review.id, "rating": "good"})
    assert not again.ok and "already finished" in again.summary


def test_completing_review_block_rates_its_reviews(ex: ToolExecutor, seeded: Repository) -> None:
    block = next(b for b in seeded.list_blocks(TODAY, TODAY) if b.block_type == config.REVIEW)
    review = next(r for r in seeded.list_reviews()
                  if r.subject_id == block.subject_id and r.unit_number == block.unit_number)
    assert ex.execute("complete_block", {"id": block.id, "rating": "easy"}).ok
    after = seeded.get_review(review.id)
    assert after is not None and after.last_rating == "easy" and after.stage_index == review.stage_index + 2
    remaining = [b for b in seeded.list_blocks(TODAY, TODAY) if b.block_type == config.REVIEW
                 and b.status == "pending" and b.subject_id == block.subject_id
                 and b.unit_number == block.unit_number]
    assert remaining == [], "a finished review is not planned again today"


def test_done_date_type(ex: ToolExecutor, seeded: Repository) -> None:
    dl = _dl(seeded)
    assert ex.execute("log_study", {"subject": "Deep Learning", "units": 1}).ok
    unit = next(u for u in seeded.list_units(dl) if u.number == 4)
    assert isinstance(unit.done_date, date) and unit.status == "done"
