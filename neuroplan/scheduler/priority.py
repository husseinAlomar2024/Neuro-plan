"""Priority weight W for one subject on one day (SPEC 4.1 step 3). Pure functions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from .. import config


@dataclass(frozen=True)
class PriorityFactors:
    days_until: int
    urgency: float
    backlog_pressure: float
    difficulty_factor: float
    mastery_deficit: float
    exam_scope_boost: float
    weight: float
    assessment_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def compute_priority(difficulty: int, mastery: int, backlog_units: int,
                     days_to_assessment: Optional[int], undone_in_range: int = 0,
                     range_size: int = 0, assessment_id: Optional[int] = None) -> PriorityFactors:
    """``days_to_assessment`` is (assessment date - day) or None when no assessment is upcoming."""
    if days_to_assessment is None:
        days_until = config.NO_ASSESSMENT_DAYS
    else:
        days_until = max(1, days_to_assessment)
    urgency = 1 + config.URGENCY_NUMERATOR / days_until
    backlog_pressure = 1 + config.BACKLOG_COEF * max(0, backlog_units)
    difficulty_factor = 1 + config.DIFFICULTY_COEF * (difficulty - config.DIFFICULTY_PIVOT)
    mastery_deficit = 1 + config.MASTERY_COEF * (config.MASTERY_MAX - mastery)
    if days_to_assessment is not None and days_until <= config.SCOPE_BOOST_DAYS:
        exam_scope_boost = 1 + config.SCOPE_BOOST_COEF * undone_in_range / max(1, range_size)
    else:
        exam_scope_boost = 1.0
    weight = urgency * backlog_pressure * difficulty_factor * mastery_deficit * exam_scope_boost
    return PriorityFactors(days_until, urgency, backlog_pressure, difficulty_factor, mastery_deficit,
                           exam_scope_boost, weight, assessment_id)


def water_fill(total: float, weights: dict[int, float], ceilings: dict[int, float]) -> tuple[dict[int, float], float]:
    """Share ``total`` proportionally to ``weights``; clamp each key to its ceiling and redistribute
    the excess to the unclamped keys until stable. Returns (allocation, leftover)."""
    alloc = {k: 0.0 for k in weights}
    active = sorted(k for k in weights if ceilings.get(k, 0.0) > 1e-9 and weights[k] > 0)
    remaining = max(0.0, total)
    while active and remaining > 1e-9:
        total_w = sum(weights[k] for k in active)
        capped = [k for k in active
                  if alloc[k] + remaining * weights[k] / total_w >= ceilings[k] - 1e-9]
        if not capped:
            for k in active:
                alloc[k] += remaining * weights[k] / total_w
            remaining = 0.0
            break
        for k in capped:
            remaining -= ceilings[k] - alloc[k]
            alloc[k] = ceilings[k]
            active.remove(k)
    return alloc, max(0.0, remaining)
