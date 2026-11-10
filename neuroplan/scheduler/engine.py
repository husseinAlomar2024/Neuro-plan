"""generate_plan(state, start, days) -> PlanResult. Pure and deterministic (SPEC section 4)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .. import config
from ..models import (Assessment, PlanResult, ProjectedReview, State, StudyBlock, Subject, Unit)
from .feasibility import SimTrace, assess_risks
from .priority import PriorityFactors, compute_priority, water_fill

_EPS = 1e-6
_REVIEW_UNITS_RE = re.compile(r"units? (\d+)(?: & (\d+))?")
_HEAVY_RUN = max(1, config.MAX_CONSECUTIVE_HEAVY_MIN // config.QUANTUM[config.HEAVY])
_REVIEW_POM = config.POMODORO[config.REVIEW]
_REVIEW_BREAK = config.BREAK_MIN[_REVIEW_POM]


# ---------------------------------------------------------------- task text
def task_text(mastery: int, unit_number: int) -> str:
    if mastery <= 2:
        return (f"Unit {unit_number}: Concept acquisition — study actively, then write a 5-bullet "
                f"Feynman summary from memory.")
    if mastery == 3:
        return (f"Unit {unit_number}: Guided application — solve 5 problems / build the example "
                f"without looking at solutions.")
    return (f"Unit {unit_number}: Timed active recall — practice questions under time limit, "
            f"then check mistakes.")


def review_text(subject: str, units: list[int]) -> str:
    label = f"unit {units[0]}" if len(units) == 1 else f"units {units[0]} & {units[1]}"
    return (f"Review {subject} {label}: close notes, recall everything you can in 5 min, "
            f"then check and rate yourself.")


def practice_text(a: Assessment) -> str:
    return (f"Practice for {a.title}: past questions on units {a.unit_from}–{a.unit_to} "
            f"under exam conditions, then review your mistakes.")


def review_units(block: StudyBlock) -> list[int]:
    """Unit numbers covered by a REVIEW block (encoded in its task text)."""
    m = _REVIEW_UNITS_RE.search(block.task_text)
    if not m:
        return [block.unit_number]
    return [int(g) for g in m.groups() if g]


# ---------------------------------------------------------------- time helpers
def to_minutes(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 18 * 60


def to_hhmm(minutes: int) -> str:
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def in_peak_window(start_time: str, peak: str) -> bool:
    lo, hi = config.PEAK_HOURS.get(peak, (0, 24))
    hour = to_minutes(start_time) // 60
    return lo <= hour < hi if lo < hi else (hour >= lo or hour < hi)


# ---------------------------------------------------------------- simulation state
class _Sim:
    """Forward-simulated progress of one subject."""

    def __init__(self, subject: Subject, units: list[Unit], assessments: list[Assessment],
                 last_day: int) -> None:
        self.s = subject
        self.mpu = max(1, subject.minutes_per_unit)
        # [number, minutes_logged, done]
        self.units: list[list[int]] = [
            [u.number, self.mpu if u.status == "done" else min(u.minutes_logged, self.mpu), int(u.status == "done")]
            for u in sorted(units, key=lambda u: u.number)]
        self.assessments = sorted(assessments, key=lambda a: (a.date, a.id))
        self.carry = 0.0
        self.last_day = last_day
        self.last_unit = self.max_done()   # highest unit number any block has referred to
        self.q = config.QUANTUM[subject.category]
        self.pom = config.POMODORO[subject.category]
        self.brk = config.BREAK_MIN[self.pom]
        self.cost = self.q + self.brk

    def done_count(self) -> int:
        return sum(u[2] for u in self.units)

    def backlog(self) -> int:
        return max(0, self.s.expected_units_by_now - self.done_count())

    def next_index(self) -> Optional[int]:
        for i, u in enumerate(self.units):
            if not u[2]:
                return i
        return None

    def max_done(self) -> int:
        return max((u[0] for u in self.units if u[2]), default=0)

    def remaining_min(self) -> int:
        return sum(self.mpu - u[1] for u in self.units if not u[2])

    def range_remaining(self, a: Assessment) -> int:
        return sum(self.mpu - u[1] for u in self.units if not u[2] and a.unit_from <= u[0] <= a.unit_to)

    def undone_in_range(self, a: Assessment) -> int:
        return sum(1 for u in self.units if not u[2] and a.unit_from <= u[0] <= a.unit_to)

    def nearest(self, d: date) -> Optional[Assessment]:
        for a in self.assessments:
            if a.date >= d:
                return a
        return None

    def exam_close(self, d: date) -> bool:
        return any(0 <= (a.date - d).days <= config.EXAM_CLOSE_DAYS for a in self.assessments)

    def work_kind(self, d: date, practiced: bool = False) -> Optional[str]:
        """'unit' (study the next unit), 'practice' (assessment practice) or None (nothing to do).
        Practice applies once the upcoming assessment's range is fully done: it is all that is left
        when no unit remains; otherwise it takes one block per day inside the exam-close window."""
        has_undone = self.next_index() is not None
        a = self.nearest(d)
        if a is not None and self.undone_in_range(a) == 0:
            if not has_undone:
                return "practice"
            if (a.date - d).days <= config.EXAM_CLOSE_DAYS and not practiced:
                return "practice"
        return "unit" if has_undone else None

    def add_minutes(self, minutes: int) -> list[int]:
        """Log minutes on the next undone unit; overflow flows on. Returns units that became done."""
        finished: list[int] = []
        left = minutes
        while left > 0:
            i = self.next_index()
            if i is None:
                break
            u = self.units[i]
            take = min(left, self.mpu - u[1])
            u[1] += take
            left -= take
            if u[1] >= self.mpu:
                u[2] = 1
                finished.append(u[0])
            elif take == 0:
                break
        return finished


@dataclass
class _Cand:
    sim: _Sim
    factors: PriorityFactors
    max_blocks: int
    backlog: int = 0
    budget: float = 0.0
    n: int = 0
    debited: int = 0          # blocks paid from the budget (extra fill blocks are not)
    blocks: list[StudyBlock] = field(default_factory=list)

    @property
    def w(self) -> float:
        return self.factors.weight

    @property
    def heavy(self) -> bool:
        return self.sim.s.category == config.HEAVY


def _heavy_feasible(h: int, separators: int, run: int = 0) -> bool:
    return h <= (_HEAVY_RUN - run) + _HEAVY_RUN * separators


# ---------------------------------------------------------------- ordering (SPEC 4.1 step 7)
def _order_day(reviews: list[StudyBlock], cands: list[_Cand], in_peak: bool) -> list[StudyBlock]:
    queues = {c.sim.s.id: list(c.blocks) for c in cands if c.blocks}
    by_id = {c.sim.s.id: c for c in cands}
    h = sum(len(q) for sid, q in queues.items() if by_id[sid].heavy)
    n = sum(len(q) for sid, q in queues.items() if not by_id[sid].heavy)
    rest = list(reviews)
    seq: list[StudyBlock] = []
    # (a) one review first as warm-up, unless every review is needed to separate HEAVY runs
    if rest and (_heavy_feasible(h, n + len(rest) - 1) or not _heavy_feasible(h, n + len(rest))):
        seq.append(rest.pop(0))
    reviews_separate = not _heavy_feasible(h, n)
    run = 0
    prev_cat: Optional[str] = None
    first = True
    while queues:
        subs = [by_id[sid] for sid in queues]
        if first and in_peak and any(c.heavy for c in subs):
            pref = sorted(subs, key=lambda c: (not c.heavy, -c.sim.s.difficulty if c.heavy else 0,
                                               -c.w, c.sim.s.id))
        elif first:
            pref = sorted(subs, key=lambda c: (-c.w, c.sim.s.id))
        else:
            pref = sorted(subs, key=lambda c: (c.sim.s.category == prev_cat, -c.w, c.sim.s.id))
        seps = len(rest) if reviews_separate else 0
        chosen: Optional[_Cand] = None
        for c in pref:
            if c.heavy:
                ok = run + 1 <= _HEAVY_RUN and _heavy_feasible(h - 1, n + seps, run + 1)
            else:
                ok = _heavy_feasible(h, n - 1 + seps, 0)
            if ok:
                chosen = c
                break
        if chosen is None:
            if reviews_separate and rest:
                seq.append(rest.pop(0))
                run, prev_cat = 0, config.REVIEW
                continue
            chosen = pref[0]
        first = False
        sid = chosen.sim.s.id
        seq.append(queues[sid].pop(0))
        if not queues[sid]:
            del queues[sid]
        if chosen.heavy:
            run, h = run + 1, h - 1
        else:
            run, n = 0, n - 1
        prev_cat = chosen.sim.s.category
    seq.extend(rest)  # (d) remaining reviews last
    return seq


def _layout(seq: list[StudyBlock], session_start: str, kept: list[StudyBlock]) -> None:
    """Assign start_time / order_index sequentially, stepping around kept blocks' time slots."""
    busy = sorted((to_minutes(b.start_time),
                   to_minutes(b.start_time) + b.duration_min + config.BREAK_MIN.get(b.pomodoro, 0))
                  for b in kept if b.start_time)
    t = to_minutes(session_start)
    order = max((b.order_index for b in kept), default=-1) + 1
    for b in seq:
        moved = True
        while moved:
            moved = False
            for lo, hi in busy:
                if t < hi and t + b.duration_min > lo:
                    t, moved = hi, True
        b.start_time = to_hhmm(t)
        b.order_index = order
        order += 1
        t += b.duration_min + config.BREAK_MIN[b.pomodoro]


# ---------------------------------------------------------------- main entry
def generate_plan(state: State, start: date, days: int) -> PlanResult:
    subjects = sorted((s for s in state.subjects if s.active), key=lambda s: s.id)
    active_ids = {s.id for s in subjects}
    by_subject_assess: dict[int, list[Assessment]] = {}
    for a in state.assessments:
        by_subject_assess.setdefault(a.subject_id, []).append(a)
    sims: list[_Sim] = []
    for s in subjects:
        last = state.last_studied.get(s.id)
        last_day = min(-1, (last - start).days) if last else -1
        sims.append(_Sim(s, state.units.get(s.id, []), by_subject_assess.get(s.id, []), last_day))
    sim_by_id = {sim.s.id: sim for sim in sims}

    queue: list[ProjectedReview] = [
        ProjectedReview(r.subject_id, r.unit_number, r.stage_index, r.due_date, r.id)
        for r in state.reviews if not r.done and r.subject_id in active_ids]
    projected: list[ProjectedReview] = []

    kept_by_date: dict[date, list[StudyBlock]] = {}
    for b in state.blocks:
        if b.plan_date >= start and (b.status in ("done", "partial", "skipped")
                                     or (b.status == "pending" and b.locked)):
            kept_by_date.setdefault(b.plan_date, []).append(b)

    future = [(a.date - start).days for a in state.assessments
              if a.subject_id in active_ids and a.date >= start]
    reach = min(config.FEASIBILITY_MAX_DAYS, max(future) + 1) if future else 0
    sim_days = max(days, reach)
    trace = SimTrace(start=start, days=sim_days)
    out_blocks: list[StudyBlock] = []
    explanations: dict[int, dict] = {
        s.id: {"subject": s.name, "category": s.category, "days": []} for s in subjects}
    free_minutes: dict[date, int] = {}

    def advance(pr: ProjectedReview, day: date, record: bool) -> None:
        queue.remove(pr)
        nxt = pr.stage_index + 1
        if nxt < len(config.REVIEW_STAGES_DAYS):
            new = ProjectedReview(pr.subject_id, pr.unit_number, nxt,
                                  day + timedelta(days=config.REVIEW_STAGES_DAYS[nxt]))
            queue.append(new)
            if record:
                projected.append(new)

    def unit_done(sim: _Sim, unit_number: int, day: date, record: bool) -> None:
        new = ProjectedReview(sim.s.id, unit_number, 0,
                              day + timedelta(days=config.REVIEW_STAGES_DAYS[0]))
        queue.append(new)
        if record:
            projected.append(new)

    for k in range(sim_days):
        d = start + timedelta(days=k)
        record = k < days
        av = state.availability.get(d.weekday())
        ov = state.overrides.get(d)
        capacity = ov.minutes if ov is not None else (av.minutes if av else 0)
        session_start = (ov.start_time if ov is not None and ov.start_time
                         else (av.start_time if av else config.DEFAULT_START_TIME))
        peak = av.peak_window if av else config.DEFAULT_PEAK
        trace.capacity.append(capacity)
        for sim in sims:
            for a in sim.assessments:
                if a.date == d:
                    trace.remaining_at[a.id] = sim.range_remaining(a)

        # ---- step 8: kept blocks (locked / done / partial; skipped slots count as used time)
        kept = sorted(kept_by_date.get(d, []), key=lambda b: (b.order_index, b.id))
        kept_cost = 0
        kept_subject: dict[int, int] = {}
        kept_review_focus = 0
        breaks: list[int] = []
        for b in kept:
            mins = b.actual_min if b.status in ("done", "partial") and b.actual_min is not None else b.duration_min
            brk = config.BREAK_MIN.get(b.pomodoro, 0)
            kept_cost += mins + brk
            breaks.append(brk)
            sim = sim_by_id.get(b.subject_id)
            if b.status == "skipped" or sim is None:
                continue
            kept_subject[b.subject_id] = kept_subject.get(b.subject_id, 0) + mins
            if b.block_type == config.REVIEW:
                kept_review_focus += mins
                if b.status == "pending":
                    covered = set(review_units(b))
                    for pr in [p for p in queue if p.subject_id == b.subject_id
                               and p.unit_number in covered and p.due_date <= d]:
                        advance(pr, d, record)
                continue
            sim.last_day = k
            if b.status == "pending" and b.block_type in (config.CATCHUP, config.PROGRESS):
                trace.add_planned(b.subject_id, k, b.duration_min)
                for num in sim.add_minutes(b.duration_min):
                    unit_done(sim, num, d, record)

        # ---- step 2: reviews first
        review_cost_each = config.REVIEW_BLOCK_MIN + _REVIEW_BREAK
        budget_focus = config.REVIEW_CAP_SHARE * capacity - kept_review_focus
        n_reviews_max = max(0, int((budget_focus + _EPS) // config.REVIEW_BLOCK_MIN))
        n_reviews_max = min(n_reviews_max, max(0, int((capacity - kept_cost) // review_cost_each)))
        due = sorted((p for p in queue if p.due_date <= d),
                     key=lambda p: (p.due_date, p.subject_id, p.unit_number, p.stage_index))
        review_blocks: list[StudyBlock] = []
        review_subject: dict[int, int] = {}
        taken: set[int] = set()
        for i, pr in enumerate(due):
            if len(review_blocks) >= n_reviews_max:
                break
            if i in taken:
                continue
            group = [pr]
            taken.add(i)
            for j in range(i + 1, len(due)):
                if len(group) >= config.REVIEW_UNITS_PER_BLOCK:
                    break
                if j not in taken and due[j].subject_id == pr.subject_id and due[j].unit_number != pr.unit_number:
                    group.append(due[j])
                    taken.add(j)
            sim = sim_by_id[pr.subject_id]
            review_blocks.append(StudyBlock(
                0, d, "", pr.subject_id, group[0].unit_number, config.REVIEW, _REVIEW_POM,
                config.REVIEW_BLOCK_MIN, review_text(sim.s.name, [g.unit_number for g in group])))
            review_subject[pr.subject_id] = review_subject.get(pr.subject_id, 0) + config.REVIEW_BLOCK_MIN
            for g in group:
                advance(g, d, record)
        breaks.extend([_REVIEW_BREAK] * len(review_blocks))
        room = max(0.0, capacity - kept_cost - len(review_blocks) * review_cost_each)

        # ---- step 3: priority
        cands: list[_Cand] = []
        for sim in sims:
            kind = sim.work_kind(d)
            if kind is None:
                continue
            a = sim.nearest(d)
            factors = compute_priority(
                sim.s.difficulty, sim.s.mastery, sim.backlog(),
                (a.date - d).days if a else None,
                sim.undone_in_range(a) if a else 0,
                (a.unit_to - a.unit_from + 1) if a else 0,
                a.id if a else None)
            share = (config.DAILY_CAP_SHARE_EXAM if sim.s.id in state.cap_boost or sim.exam_close(d)
                     else config.DAILY_CAP_SHARE)
            trace.set_share(sim.s.id, k, share)
            cap_focus = (max(share * capacity, sim.q) - kept_subject.get(sim.s.id, 0)
                         - review_subject.get(sim.s.id, 0))
            max_blocks = max(0, int((cap_focus + _EPS) // sim.q))
            if sim.next_index() is not None:
                work = math.ceil(sim.remaining_min() / sim.q) + (1 if kind == "practice" else 0)
                max_blocks = min(max_blocks, work)
            cands.append(_Cand(sim, factors, max_blocks, sim.backlog()))

        # ---- step 4: allocation with caps (water-filling)
        weights = {c.sim.s.id: c.w for c in cands}
        ceilings = {c.sim.s.id: max(0.0, c.max_blocks * c.sim.cost - c.sim.carry) for c in cands}
        alloc, _ = water_fill(room, weights, ceilings)

        # ---- step 5: quantize with carry (deficit round-robin)
        used = 0.0
        for c in cands:
            c.budget = alloc[c.sim.s.id] + c.sim.carry
        by_weight = sorted(cands, key=lambda c: (-c.w, c.sim.s.id))
        for c in by_weight:      # starvation guard: nobody waits longer than STARVATION_GAP_DAYS
            if (k - c.sim.last_day >= config.STARVATION_GAP_DAYS and c.max_blocks >= 1
                    and used + c.sim.cost <= room + _EPS):
                c.n += 1
                c.debited += 1
                c.budget -= c.sim.cost
                used += c.sim.cost
        while True:
            ready = [c for c in cands if c.budget >= c.sim.cost - _EPS and c.n < c.max_blocks
                     and used + c.sim.cost <= room + _EPS]
            if not ready:
                break
            c = max(ready, key=lambda c: (c.budget, c.w, -c.sim.s.id))
            c.n += 1
            c.debited += 1
            c.budget -= c.sim.cost
            used += c.sim.cost

        def drop_block(c: _Cand) -> None:
            nonlocal used
            c.n -= 1
            used -= c.sim.cost
            if c.debited > c.n:
                c.debited -= 1
                c.budget += c.sim.cost

        def try_fill(pool: list[_Cand]) -> Optional[_Cand]:
            # the last block of the day has no trailing break, so one break may be waived
            nonlocal used
            current = breaks + [x.sim.brk for x in cands for _ in range(x.n)]
            for c in pool:
                if c.n < c.max_blocks and used + c.sim.cost - min(current + [c.sim.brk]) <= room + _EPS:
                    c.n += 1          # an extra block: the carry is left untouched (SPEC step 5)
                    used += c.sim.cost
                    return c
            return None

        filled = try_fill(by_weight)
        while filled is not None:
            filled = try_fill(by_weight)

        # keep HEAVY runs separable: h <= run * (separators + 1) whenever a non-HEAVY block exists
        def heavy_count() -> int:
            return sum(c.n for c in cands if c.heavy)

        def sep_count() -> int:
            return sum(c.n for c in cands if not c.heavy) + len(review_blocks)

        light = [c for c in by_weight if not c.heavy]
        while sep_count() > 0 and not _heavy_feasible(heavy_count(), sep_count()):
            victim = min((c for c in cands if c.heavy and c.n > 0), key=lambda c: (c.w, -c.sim.s.id))
            drop_block(victim)
            try_fill(light)

        # ---- step 6: block content
        for c in cands:
            sim = c.sim
            practiced = False
            for _ in range(c.n):
                kind = sim.work_kind(d, practiced)
                if kind is None:
                    break
                if kind == "practice":
                    practiced = True
                    a = sim.nearest(d)
                    assert a is not None
                    sim.last_unit = max(sim.last_unit, sim.max_done())
                    blk = StudyBlock(0, d, "", sim.s.id, sim.last_unit, config.PRACTICE, sim.pom,
                                     sim.q, practice_text(a))
                else:
                    idx = sim.next_index()
                    assert idx is not None
                    num = sim.units[idx][0]
                    sim.last_unit = max(sim.last_unit, num)
                    btype = config.CATCHUP if num <= sim.s.expected_units_by_now else config.PROGRESS
                    blk = StudyBlock(0, d, "", sim.s.id, num, btype, sim.pom, sim.q,
                                     task_text(sim.s.mastery, num))
                    trace.add_planned(sim.s.id, k, sim.q)
                    for done_num in sim.add_minutes(sim.q):
                        unit_done(sim, done_num, d, record)
                c.blocks.append(blk)
            made = len(c.blocks)
            while c.n > made:
                drop_block(c)
            sim.carry = max(-float(sim.cost), min(float(sim.cost), c.budget))
            if made:
                sim.last_day = k

        if record:
            seq = _order_day(review_blocks, cands, in_peak_window(session_start, peak))
            _layout(seq, session_start, kept)
            out_blocks.extend(seq)
            free = int(round(room - used))
            if free > 0:
                free_minutes[d] = free
            for c in cands:
                entry = c.factors.to_dict()
                entry.update({"date": d.isoformat(), "blocks": len(c.blocks),
                              "allocated_min": len(c.blocks) * c.sim.q,
                              "share_of_day": round(alloc[c.sim.s.id] / room, 3) if room > 0 else 0.0,
                              "backlog_units": c.backlog})
                explanations[c.sim.s.id]["days"].append(entry)

    risks = assess_risks(state, start, trace)
    return PlanResult(out_blocks, projected, risks, explanations, free_minutes)
