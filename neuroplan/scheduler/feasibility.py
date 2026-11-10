"""Feasibility check and risk report (SPEC 4.2). Pure functions over the simulation trace."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import config
from ..models import Risk, RiskOption, State


@dataclass
class SimTrace:
    """What the engine's forward simulation recorded, per simulated day index."""
    start: date
    days: int
    capacity: list[int] = field(default_factory=list)
    planned: dict[int, dict[int, int]] = field(default_factory=dict)     # subject -> day -> focus min
    share: dict[int, dict[int, float]] = field(default_factory=dict)     # subject -> day -> cap share
    remaining_at: dict[int, int] = field(default_factory=dict)           # assessment -> undone min at its date

    def add_planned(self, subject_id: int, day: int, minutes: int) -> None:
        per = self.planned.setdefault(subject_id, {})
        per[day] = per.get(day, 0) + minutes

    def set_share(self, subject_id: int, day: int, share: float) -> None:
        self.share.setdefault(subject_id, {})[day] = share


def _unit_order(state: State, subject_id: int, undone: list[int], start: date) -> list[int]:
    """Undone units ordered by how heavily upcoming assessments cover them (heaviest first)."""
    score = {n: 0.0 for n in undone}
    for a in state.assessments:
        if a.subject_id != subject_id or a.date < start:
            continue
        size = max(1, a.unit_to - a.unit_from + 1)
        for n in undone:
            if a.unit_from <= n <= a.unit_to:
                score[n] += max(1, a.weight_pct) / size
    return sorted(undone, key=lambda n: (-score[n], n))


def assess_risks(state: State, start: date, trace: SimTrace) -> list[Risk]:
    subjects = {s.id: s for s in state.subjects if s.active}
    risks: list[Risk] = []
    for a in sorted(state.assessments, key=lambda a: (a.date, a.id)):
        subject = subjects.get(a.subject_id)
        shortfall = trace.remaining_at.get(a.id, 0)
        if subject is None or a.date < start or shortfall <= 0:
            continue
        mpu = max(1, subject.minutes_per_unit)
        undone = [u for u in state.units.get(subject.id, [])
                  if u.status != "done" and a.unit_from <= u.number <= a.unit_to]
        needed = sum(mpu - min(u.minutes_logged, mpu) for u in undone)
        available = max(0, needed - shortfall)
        days_to = (a.date - start).days
        days_left = max(1, days_to)

        step = config.RISK_ROUND_MIN
        extra = int(math.ceil(shortfall / days_left / step) * step)
        overrides = []
        for k in range(days_left):
            cap = trace.capacity[k] if k < len(trace.capacity) else 0
            overrides.append({"tool": "set_day_override",
                              "args": {"date": (start + timedelta(days=k)).isoformat(),
                                       "minutes": min(config.MAX_DAY_MINUTES, cap + extra)}})
        opt_time = RiskOption(
            "extra_time", f"Add {extra} min/day until {a.date.isoformat()}",
            f"{shortfall} min short over {days_left} day(s): about {extra} extra min/day closes the gap.",
            overrides, {"extra_min_per_day": extra, "days": days_left})

        order = _unit_order(state, subject.id, [u.number for u in undone], start)
        opt_order = RiskOption(
            "prioritize", "Prioritize the most-assessed units first",
            f"Prioritize units in this order: {', '.join(str(n) for n in order)} — the most heavily "
            f"assessed material is covered first if time runs out.",
            [], {"unit_order": order})

        gain = 0.0
        for k in range(min(days_to, len(trace.capacity))):
            share = trace.share.get(subject.id, {}).get(k, config.DAILY_CAP_SHARE)
            gain += max(0.0, config.DAILY_CAP_SHARE_EXAM - share) * trace.capacity[k]
        gain_min = int(gain)
        pct = int(config.DAILY_CAP_SHARE_EXAM * 100)
        if gain_min > 0:
            detail = (f"Raising {subject.name}'s daily cap to {pct}% frees up to {gain_min} min for it "
                      f"before {a.date.isoformat()}; other subjects lose up to {gain_min} min.")
        else:
            detail = (f"{subject.name} is already at the {pct}% exam cap on those days, so this adds "
                      f"0 min; other subjects lose nothing.")
        opt_cap = RiskOption(
            "raise_cap", f"Raise {subject.name}'s daily cap to {pct}%", detail,
            [{"tool": "set_cap_boost", "args": {"subject": subject.name, "enabled": True}}],
            {"gain_min": gain_min, "cost_other_subjects_min": gain_min})

        risks.append(Risk(a.id, subject.id, subject.name, a.title, a.date, needed, available, shortfall,
                          [opt_time, opt_order, opt_cap]))
    return risks
