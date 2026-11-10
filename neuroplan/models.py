"""Dataclasses shared by the repository, scheduler, tools and coach."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class Subject:
    id: int
    name: str
    category: str
    difficulty: int
    mastery: int
    minutes_per_unit: int
    expected_units_by_now: int
    color_hex: str
    active: bool = True
    created_at: str = ""


@dataclass
class Unit:
    id: int
    subject_id: int
    number: int
    title: str = ""
    minutes_logged: int = 0
    status: str = "todo"
    done_date: Optional[date] = None


@dataclass
class Assessment:
    id: int
    subject_id: int
    kind: str
    title: str
    date: date
    weight_pct: int
    unit_from: int
    unit_to: int


@dataclass
class Availability:
    weekday: int
    minutes: int
    start_time: str
    peak_window: str


@dataclass
class AvailabilityOverride:
    date: date
    minutes: int
    start_time: Optional[str] = None


@dataclass
class StudyBlock:
    id: int
    plan_date: date
    start_time: str
    subject_id: int
    unit_number: int
    block_type: str
    pomodoro: str
    duration_min: int
    task_text: str
    status: str = "pending"
    actual_min: Optional[int] = None
    locked: bool = False
    order_index: int = 0
    generated_at: str = ""


@dataclass
class Review:
    id: int
    subject_id: int
    unit_number: int
    stage_index: int
    due_date: date
    last_rating: Optional[str] = None
    done: bool = False


@dataclass
class ChatMessage:
    id: int
    ts: str
    role: str  # user | coach | system_event
    text: str
    actions: dict[str, Any] = field(default_factory=dict)
    proactive: bool = False


@dataclass
class ActionLogEntry:
    id: int
    ts: str
    source: str
    tool: str
    args: dict[str, Any]
    inverse: Optional[dict[str, Any]]
    undone: bool = False


# ---------------------------------------------------------------- scheduler I/O
@dataclass
class State:
    """Everything the pure scheduler needs. Built by scheduler.service.build_state."""
    subjects: list[Subject]
    units: dict[int, list[Unit]]
    assessments: list[Assessment]
    availability: dict[int, Availability]
    overrides: dict[date, AvailabilityOverride]
    reviews: list[Review]                      # reviews not yet done
    blocks: list[StudyBlock] = field(default_factory=list)   # existing blocks with plan_date >= start
    last_studied: dict[int, date] = field(default_factory=dict)
    cap_boost: frozenset[int] = frozenset()    # subject ids whose daily cap is raised to the exam cap


@dataclass
class ProjectedReview:
    subject_id: int
    unit_number: int
    stage_index: int
    due_date: date
    review_id: Optional[int] = None            # set for real (already stored) reviews


@dataclass
class RiskOption:
    kind: str            # extra_time | prioritize | raise_cap
    label: str
    detail: str
    actions: list[dict[str, Any]] = field(default_factory=list)   # [{"tool":..., "args":{...}}]
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Risk:
    assessment_id: int
    subject_id: int
    subject_name: str
    title: str
    date: date
    needed_min: int
    available_min: int
    shortfall_min: int
    options: list[RiskOption] = field(default_factory=list)


@dataclass
class PlanResult:
    blocks: list[StudyBlock]
    reviews_projected: list[ProjectedReview]
    risks: list[Risk]
    explanations: dict[int, dict[str, Any]]
    free_minutes: dict[date, int] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: Any = None
    needs_confirmation: bool = False
    preview: str = ""
    action_id: Optional[int] = None
    tool: str = ""
    plan_change: str = ""        # what the regeneration moved, e.g. "Today: Deep Learning −50 min"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "summary": self.summary, "data": self.data}
        if self.needs_confirmation:
            out["needs_confirmation"] = True
            out["preview"] = self.preview
        if self.plan_change:
            out["plan_change"] = self.plan_change
        return out
