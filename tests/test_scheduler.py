"""P2: scheduler acceptance tests (SPEC section 9, items 1-11 except 8b)."""
from __future__ import annotations

import copy
import time
from datetime import date, timedelta

import pytest

from neuroplan import config
from neuroplan.db.repository import Repository
from neuroplan.models import PlanResult, State, StudyBlock
from neuroplan.scheduler.engine import generate_plan, review_units
from neuroplan.scheduler.priority import compute_priority, water_fill
from neuroplan.scheduler.service import build_state, regenerate

from .conftest import TODAY

DAYS = 14


def plan_for(repo: Repository, days: int = DAYS) -> tuple[State, PlanResult]:
    state = build_state(repo, TODAY)
    return state, generate_plan(state, TODAY, days)


def by_day(blocks: list[StudyBlock]) -> dict[date, list[StudyBlock]]:
    out: dict[date, list[StudyBlock]] = {}
    for b in sorted(blocks, key=lambda b: (b.plan_date, b.order_index)):
        out.setdefault(b.plan_date, []).append(b)
    return out


def capacity(state: State, d: date) -> int:
    ov = state.overrides.get(d)
    return ov.minutes if ov else state.availability[d.weekday()].minutes


def day_total(blocks: list[StudyBlock]) -> int:
    """Scheduled minutes including breaks; the last block has no trailing break."""
    if not blocks:
        return 0
    total = sum(b.duration_min + config.BREAK_MIN[b.pomodoro] for b in blocks)
    return total - config.BREAK_MIN[blocks[-1].pomodoro]


def subject_id(repo: Repository, name: str) -> int:
    s = repo.get_subject_by_name(name)
    assert s is not None
    return s.id


def horizon() -> list[date]:
    return [TODAY + timedelta(days=k) for k in range(DAYS)]


# 1 ---------------------------------------------------------------------------
def test_daily_cap_per_subject(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    days = by_day(result.blocks)
    assert len(days) == DAYS
    for d, blocks in days.items():
        cap = capacity(state, d)
        for s in state.subjects:
            minutes = sum(b.duration_min for b in blocks if b.subject_id == s.id)
            exam_close = any(a.subject_id == s.id and 0 <= (a.date - d).days <= config.EXAM_CLOSE_DAYS
                             for a in state.assessments)
            share = config.DAILY_CAP_SHARE_EXAM if exam_close else config.DAILY_CAP_SHARE
            assert minutes <= share * cap + 1e-9, (d, s.name, minutes)


# 2 ---------------------------------------------------------------------------
def test_day_total_within_capacity(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    for d, blocks in by_day(result.blocks).items():
        assert 0 < day_total(blocks) <= capacity(state, d), d


def test_start_times_follow_durations_and_breaks(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    for d, blocks in by_day(result.blocks).items():
        hh, mm = state.availability[d.weekday()].start_time.split(":")
        t = int(hh) * 60 + int(mm)
        for b in blocks:
            assert b.start_time == f"{t // 60:02d}:{t % 60:02d}"
            t += b.duration_min + config.BREAK_MIN[b.pomodoro]


# 3 ---------------------------------------------------------------------------
def test_low_priority_subject_appears_every_three_days(seeded: Repository) -> None:
    _, result = plan_for(seeded)
    ip = subject_id(seeded, "Internet Programming")
    ip_days = {b.plan_date for b in result.blocks if b.subject_id == ip and b.block_type != config.REVIEW}
    days = horizon()
    for i in range(DAYS - 2):
        window = set(days[i:i + 3])
        assert window & ip_days, f"no Internet Programming block in {days[i]}..{days[i + 2]}"


# 4 ---------------------------------------------------------------------------
def max_heavy_run(blocks: list[StudyBlock], categories: dict[int, str]) -> int:
    best = run = 0
    for b in blocks:
        if b.block_type != config.REVIEW and categories[b.subject_id] == config.HEAVY:
            run += b.duration_min
            best = max(best, run)
        else:
            run = 0
    return best


def test_no_long_heavy_runs(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    categories = {s.id: s.category for s in state.subjects}
    for d, blocks in by_day(result.blocks).items():
        non_heavy = any(b.block_type == config.REVIEW or categories[b.subject_id] != config.HEAVY
                        for b in blocks)
        if non_heavy:
            assert max_heavy_run(blocks, categories) <= config.MAX_CONSECUTIVE_HEAVY_MIN, d


def test_heavy_runs_split_even_when_heavy_dominates(seeded: Repository) -> None:
    """Make the HEAVY subjects very urgent; runs must still be split by other blocks."""
    for name in ("Deep Learning", "Machine Learning"):
        s = seeded.get_subject_by_name(name)
        assert s is not None
        seeded.update_subject(s.id, expected_units_by_now=12, mastery=1)
    state, result = plan_for(seeded)
    categories = {s.id: s.category for s in state.subjects}
    for d, blocks in by_day(result.blocks).items():
        if any(b.block_type == config.REVIEW or categories[b.subject_id] != config.HEAVY for b in blocks):
            assert max_heavy_run(blocks, categories) <= config.MAX_CONSECUTIVE_HEAVY_MIN, d
        assert day_total(blocks) <= capacity(state, d)


# 5 ---------------------------------------------------------------------------
def test_reviews_capped_and_never_lost(seeded: Repository) -> None:
    reviews = seeded.list_reviews()
    for r in reviews:
        seeded.update_review(r.id, due_date=TODAY)       # everything is due at once
    state, result = plan_for(seeded)
    days = by_day(result.blocks)
    for d, blocks in days.items():
        review_min = sum(b.duration_min for b in blocks if b.block_type == config.REVIEW)
        assert review_min <= config.REVIEW_CAP_SHARE * capacity(state, d) + 1e-9, d
    covered: list[tuple[int, int]] = []
    first_day: dict[tuple[int, int], date] = {}
    for b in sorted(result.blocks, key=lambda b: (b.plan_date, b.order_index)):
        if b.block_type == config.REVIEW:
            for n in review_units(b):
                covered.append((b.subject_id, n))
                first_day.setdefault((b.subject_id, n), b.plan_date)
    due_now = {(r.subject_id, r.unit_number) for r in reviews}
    assert due_now <= set(covered), "every due review is scheduled somewhere in the horizon"
    assert any(first_day[key] > TODAY for key in due_now), "overflow rolls to later days"
    today_units = sum(len(review_units(b)) for b in days[TODAY] if b.block_type == config.REVIEW)
    assert today_units < len(reviews)


def test_review_block_covers_two_units_of_same_subject(seeded: Repository) -> None:
    dl = subject_id(seeded, "Deep Learning")
    for r in seeded.list_reviews():
        if r.subject_id == dl:
            seeded.update_review(r.id, due_date=TODAY - timedelta(days=5))
    _, result = plan_for(seeded)
    first = by_day(result.blocks)[TODAY][0]
    assert first.block_type == config.REVIEW and first.subject_id == dl
    assert len(review_units(first)) == 2 and first.duration_min == config.REVIEW_BLOCK_MIN
    assert first.pomodoro == config.POMODORO[config.REVIEW]


# 6 ---------------------------------------------------------------------------
def test_units_non_decreasing_and_spanning(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    ordered = sorted(result.blocks, key=lambda b: (b.plan_date, b.order_index))
    for s in state.subjects:
        mine = [b for b in ordered if b.subject_id == s.id and b.block_type != config.REVIEW]
        numbers = [b.unit_number for b in mine]
        assert numbers == sorted(numbers), s.name
        # replay: every study block must target the next undone unit at that moment
        logged = {u.number: (s.minutes_per_unit if u.status == "done" else u.minutes_logged)
                  for u in state.units[s.id]}
        for b in mine:
            if b.block_type == config.PRACTICE:
                continue
            nxt = min(n for n, m in logged.items() if m < s.minutes_per_unit)
            assert b.unit_number == nxt, (s.name, b.plan_date, b.unit_number, nxt)
            assert b.block_type == (config.CATCHUP if nxt <= s.expected_units_by_now else config.PROGRESS)
            left = b.duration_min
            while left > 0:
                n = min(n for n, m in logged.items() if m < s.minutes_per_unit)
                take = min(left, s.minutes_per_unit - logged[n])
                logged[n] += take
                left -= take
    dl = subject_id(seeded, "Deep Learning")
    dl_units = [b.unit_number for b in ordered if b.subject_id == dl and b.block_type in
                (config.CATCHUP, config.PROGRESS)]
    assert dl_units[:4] == [4, 4, 5, 6], "a 75-minute unit spans two 50-minute blocks"


def test_task_text_follows_mastery(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    mastery = {s.id: s.mastery for s in state.subjects}
    for b in result.blocks:
        if b.block_type in (config.CATCHUP, config.PROGRESS):
            m = mastery[b.subject_id]
            key = "Concept acquisition" if m <= 2 else "Guided application" if m == 3 else "Timed active recall"
            assert key in b.task_text and b.task_text.startswith(f"Unit {b.unit_number}:")
        elif b.block_type == config.REVIEW:
            assert b.task_text.startswith("Review ") and "rate yourself" in b.task_text


# 7 ---------------------------------------------------------------------------
def test_deterministic_and_pure(seeded: Repository) -> None:
    state = build_state(seeded, TODAY)
    frozen = copy.deepcopy(state)
    first = generate_plan(state, TODAY, DAYS)
    second = generate_plan(state, TODAY, DAYS)
    assert first == second
    assert state == frozen, "generate_plan must not mutate its input"


# 8 ---------------------------------------------------------------------------
def test_feasibility_risk_with_three_options(seeded: Repository) -> None:
    dl = subject_id(seeded, "Deep Learning")
    midterm = seeded.list_assessments(dl)[0]
    seeded.update_assessment(midterm.id, date=TODAY + timedelta(days=2))
    for wd in range(7):
        seeded.set_availability(wd, 180, "18:00", "night")          # 3 h/day
    _, result = plan_for(seeded)
    risk = next(r for r in result.risks if r.assessment_id == midterm.id)
    assert risk.needed_min == 3 * 75
    assert risk.shortfall_min > 0
    assert risk.needed_min - risk.available_min == risk.shortfall_min
    assert [o.kind for o in risk.options] == ["extra_time", "prioritize", "raise_cap"]
    extra, order, cap = risk.options
    assert extra.data["extra_min_per_day"] * 2 >= risk.shortfall_min
    assert len(extra.actions) == 2 and all(a["tool"] == "set_day_override" for a in extra.actions)
    assert order.data["unit_order"] == [4, 5, 6]
    assert cap.actions[0]["tool"] == "set_cap_boost" and cap.data["gain_min"] >= 0
    for option in risk.options:
        text = (option.label + " " + option.detail).lower()
        assert "skip" not in text and "drop" not in text
    assert "rioritize" in order.label


def test_no_false_risk_on_seed(seeded: Repository) -> None:
    _, result = plan_for(seeded)
    assert result.risks == []


def test_prioritize_orders_units_by_assessment_coverage(seeded: Repository) -> None:
    ml = subject_id(seeded, "Machine Learning")
    for u in seeded.list_units(ml):
        seeded.update_unit(u.id, status="todo", minutes_logged=0, done_date=None)
    for wd in range(7):
        seeded.set_availability(wd, 30, "18:00", "night")
    _, result = plan_for(seeded)
    midterm = [a for a in seeded.list_assessments(ml) if a.kind == "midterm"][0]
    risk = next(r for r in result.risks if r.assessment_id == midterm.id)
    order = risk.options[1].data["unit_order"]
    assert set(order[:3]) == {3, 4, 5}, "units covered by both the quiz and the midterm come first"


# 9 ---------------------------------------------------------------------------
def test_override_today_90_minutes(seeded: Repository) -> None:
    seeded.set_override(TODAY, 90, None)
    regenerate(seeded, TODAY)
    blocks = seeded.list_blocks(TODAY, TODAY + timedelta(days=DAYS - 1))
    week = [b for b in blocks if b.plan_date <= TODAY + timedelta(days=6)]
    today_blocks = [b for b in blocks if b.plan_date == TODAY]
    assert 0 < day_total(today_blocks) <= 90
    assert {b.plan_date for b in week} == {TODAY + timedelta(days=k) for k in range(7)}
    for s in seeded.list_subjects():
        done = sum(1 for u in seeded.list_units(s.id) if u.status == "done")
        backlog_units = set(range(done + 1, s.expected_units_by_now + 1))
        if not backlog_units:
            continue
        # every subject that is behind is being caught up during the week ...
        assert any(b.subject_id == s.id and b.block_type == config.CATCHUP for b in week), s.name
        # ... and no backlog unit falls out of the regenerated plan
        planned = {b.unit_number for b in blocks if b.subject_id == s.id and b.block_type == config.CATCHUP}
        assert backlog_units <= planned, f"{s.name}: backlog units {backlog_units - planned} not planned"
    week_catchup = sum(b.duration_min for b in week if b.block_type == config.CATCHUP)
    assert week_catchup >= 300, "the week keeps most of its time on catch-up work"


# 10 --------------------------------------------------------------------------
def test_locked_and_done_blocks_survive_regeneration(seeded: Repository) -> None:
    blocks = seeded.list_blocks(TODAY)
    study_today = [b for b in blocks if b.plan_date == TODAY and b.block_type != config.REVIEW]
    later = [b for b in blocks if b.plan_date == TODAY + timedelta(days=3)][1]
    seeded.update_block(study_today[0].id, status="done", actual_min=study_today[0].duration_min)
    seeded.update_block(study_today[1].id, locked=True)
    seeded.update_block(later.id, locked=True)
    kept_ids = [study_today[0].id, study_today[1].id, later.id]
    before = {i: seeded.get_block(i) for i in kept_ids}
    regenerate(seeded, TODAY)
    regenerate(seeded, TODAY)
    for i in kept_ids:
        assert seeded.get_block(i) == before[i]
    state = build_state(seeded, TODAY)
    for d, day_blocks in by_day(seeded.list_blocks(TODAY, TODAY + timedelta(days=DAYS - 1))).items():
        ordered = sorted(day_blocks, key=lambda b: (b.start_time, b.order_index))
        assert day_total(ordered) <= capacity(state, d), d
    # the locked block's time is not double-booked
    today_blocks = sorted((b for b in seeded.list_blocks(TODAY, TODAY)), key=lambda b: b.start_time)
    for a, b in zip(today_blocks, today_blocks[1:]):
        ah, am = map(int, a.start_time.split(":"))
        bh, bm = map(int, b.start_time.split(":"))
        assert ah * 60 + am + a.duration_min <= bh * 60 + bm


def test_skipped_block_time_is_not_refilled(seeded: Repository) -> None:
    today_blocks = [b for b in seeded.list_blocks(TODAY, TODAY)]
    planned_before = sum(b.duration_min for b in today_blocks)
    victim = next(b for b in today_blocks if b.block_type != config.REVIEW)
    seeded.update_block(victim.id, status="skipped")
    regenerate(seeded, TODAY)
    after = seeded.list_blocks(TODAY, TODAY)
    pending_min = sum(b.duration_min for b in after if b.status == "pending")
    assert pending_min <= planned_before - victim.duration_min


# 11 --------------------------------------------------------------------------
def test_performance_six_subjects(seeded: Repository) -> None:
    for i, cat in enumerate((config.CODING, config.MEMORY)):
        sid = seeded.insert_subject(f"Extra {i}", cat, 3, 3, 60, 4, "#123456")
        seeded.set_total_units(sid, 15)
        seeded.insert_assessment(sid, "final", f"Extra {i} final", TODAY + timedelta(days=20 + i), 40, 1, 15)
    state = build_state(seeded, TODAY)
    assert len(state.subjects) == 6
    best = min(_timed(lambda: generate_plan(state, TODAY, DAYS)) for _ in range(3))
    assert best < 0.2, f"generate_plan took {best * 1000:.0f} ms"
    assert min(_timed(lambda: regenerate(seeded, TODAY)) for _ in range(3)) < 0.2


def _timed(fn) -> float:  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


# extra unit tests ------------------------------------------------------------
def test_priority_formula_numbers() -> None:
    f = compute_priority(difficulty=5, mastery=2, backlog_units=4, days_to_assessment=10,
                         undone_in_range=3, range_size=6)
    assert f.urgency == pytest.approx(2.4)
    assert f.backlog_pressure == pytest.approx(2.8)
    assert f.difficulty_factor == pytest.approx(1.3)
    assert f.mastery_deficit == pytest.approx(1.36)
    assert f.exam_scope_boost == pytest.approx(1.25)
    assert f.weight == pytest.approx(2.4 * 2.8 * 1.3 * 1.36 * 1.25)
    none = compute_priority(3, 3, 0, None)
    assert none.days_until == config.NO_ASSESSMENT_DAYS and none.exam_scope_boost == 1.0
    assert compute_priority(3, 3, 0, 0).days_until == 1
    assert compute_priority(3, 3, 0, 20, 5, 5).exam_scope_boost == 1.0


def test_water_fill_redistributes_capped_excess() -> None:
    alloc, left = water_fill(100, {1: 3.0, 2: 1.0}, {1: 50.0, 2: 1000.0})
    assert alloc == {1: 50.0, 2: 50.0} and left == 0
    alloc, left = water_fill(100, {1: 1.0, 2: 1.0}, {1: 30.0, 2: 30.0})
    assert alloc == {1: 30.0, 2: 30.0} and left == pytest.approx(40.0)
    alloc, left = water_fill(90, {1: 2.0, 2: 1.0}, {1: 500.0, 2: 500.0})
    assert alloc[1] == pytest.approx(60.0) and alloc[2] == pytest.approx(30.0)


def test_explanations_quote_real_numbers(seeded: Repository) -> None:
    state, result = plan_for(seeded)
    dl = subject_id(seeded, "Deep Learning")
    day0 = result.explanations[dl]["days"][0]
    assert day0["date"] == TODAY.isoformat()
    assert day0["urgency"] == pytest.approx(2.4) and day0["days_until"] == 10
    assert day0["backlog_units"] == 4
    planned = sum(b.duration_min for b in result.blocks if b.subject_id == dl and b.plan_date == TODAY
                  and b.block_type != config.REVIEW)
    assert day0["allocated_min"] == planned
    assert len(result.explanations[dl]["days"]) == DAYS


def test_practice_blocks_when_assessment_range_is_done(seeded: Repository) -> None:
    ip = seeded.get_subject_by_name("Internet Programming")
    assert ip is not None
    for u in seeded.list_units(ip.id):
        seeded.update_unit(u.id, status="done", minutes_logged=ip.minutes_per_unit, done_date=TODAY)
    _, result = plan_for(seeded)
    mine = [b for b in result.blocks if b.subject_id == ip.id and b.block_type != config.REVIEW]
    assert mine and all(b.block_type == config.PRACTICE for b in mine)
    assert "Practice for Internet Programming project" in mine[0].task_text


def test_finished_subject_without_assessment_gets_no_blocks(seeded: Repository) -> None:
    ip = seeded.get_subject_by_name("Internet Programming")
    assert ip is not None
    for u in seeded.list_units(ip.id):
        seeded.update_unit(u.id, status="done", minutes_logged=ip.minutes_per_unit, done_date=TODAY)
    for a in seeded.list_assessments(ip.id):
        seeded.delete_assessment(a.id)
    _, result = plan_for(seeded)
    assert not [b for b in result.blocks if b.subject_id == ip.id and b.block_type != config.REVIEW]


def test_single_subject_leftover_reported_as_free_minutes(repo: Repository) -> None:
    sid = repo.insert_subject("Solo", config.HEAVY, 3, 3, 60, 0, "#000000")
    repo.set_total_units(sid, 20)
    for wd in range(7):
        repo.set_availability(wd, 300, "09:00", "morning")
    state, result = plan_for(repo, 3)
    for d, blocks in by_day(result.blocks).items():
        assert sum(b.duration_min for b in blocks) <= config.DAILY_CAP_SHARE * 300
        assert result.free_minutes[d] > 0


def test_inactive_subjects_are_not_scheduled(seeded: Repository) -> None:
    ml = subject_id(seeded, "Machine Learning")
    seeded.update_subject(ml, active=False)
    _, result = plan_for(seeded)
    assert all(b.subject_id != ml for b in result.blocks)


def test_empty_database_plans_nothing(repo: Repository) -> None:
    _, result = plan_for(repo)
    assert result.blocks == [] and result.risks == []
