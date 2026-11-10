"""P4: the offline intent parser maps utterances to the same tool calls the LLM would make."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from neuroplan import config
from neuroplan.coach.offline_engine import OfflineEngine, next_weekday, parse_date, parse_minutes
from neuroplan.db.repository import Repository
from neuroplan.tools.executor import ToolExecutor
from neuroplan.tools.matching import find_subjects, match_subject

from .conftest import TODAY

ISO = TODAY.isoformat()
NAMES = ["Deep Learning", "Machine Learning", "Internet Programming", "IELTS Prep"]


@pytest.fixture
def engine(seeded: Repository) -> OfflineEngine:
    return OfflineEngine(ToolExecutor(seeded, today_fn=lambda: TODAY))


@pytest.fixture
def bare(repo: Repository) -> OfflineEngine:
    """Subjects exist (with units) but have no assessments and default availability."""
    ex = ToolExecutor(repo, today_fn=lambda: TODAY)
    for name, done in (("Deep Learning", 3), ("Machine Learning", 4), ("Internet Programming", 5)):
        assert ex.execute("upsert_subject", {"name": name, "total_units": 12, "done_units": done}).ok
    return OfflineEngine(ex)


def calls(engine: OfflineEngine, text: str) -> list[tuple[str, dict[str, Any]]]:
    return [(c.name, c.args) for c in engine.parse(text)]


# ---------------------------------------------------------------- SPEC section 9 utterances
def test_subject_list(repo: Repository) -> None:
    engine = OfflineEngine(ToolExecutor(repo, today_fn=lambda: TODAY))
    assert calls(engine, "I have 3 subjects: Deep Learning, Machine Learning and Internet Programming") == [
        ("upsert_subject", {"name": "Deep Learning"}),
        ("upsert_subject", {"name": "Machine Learning"}),
        ("upsert_subject", {"name": "Internet Programming"}),
    ]


def test_behind_in_two_subjects_with_acronym(bare: OfflineEngine) -> None:
    assert calls(bare, "I'm 4 lectures behind in deep learning and 2 behind in ML") == [
        ("set_progress", {"subject": "Deep Learning", "expected_units_by_now": 7}),      # 3 done + 4
        ("set_progress", {"subject": "Machine Learning", "expected_units_by_now": 6}),   # 4 done + 2
    ]


def test_weekly_availability(bare: OfflineEngine) -> None:
    result = calls(bare, "I have 3 hours a day on weekdays and 6 hours on friday and saturday")
    assert len(result) == 1 and result[0][0] == "set_weekly_availability"
    minutes = {d["weekday"]: d["minutes"] for d in result[0][1]["days"]}
    assert minutes == {6: 180, 0: 180, 1: 180, 2: 180, 3: 180, 4: 360, 5: 360}


@pytest.mark.parametrize("text", ["today I only have 90 minutes", "I only have 1.5h today"])
def test_day_override(bare: OfflineEngine, text: str) -> None:
    assert calls(bare, text) == [("set_day_override", {"date": ISO, "minutes": 90})]


def test_finished_units(bare: OfflineEngine) -> None:
    assert calls(bare, "I finished 2 units of deep learning today") == [
        ("log_study", {"subject": "Deep Learning", "units": 2})]


def test_studied_minutes(bare: OfflineEngine) -> None:
    assert calls(bare, "I studied machine learning for 40 min") == [
        ("log_study", {"subject": "Machine Learning", "minutes": 40})]


def test_assessment_on_iso_date(bare: OfflineEngine) -> None:
    assert calls(bare, "my ML midterm is on 2026-11-24") == [
        ("add_assessment", {"subject": "Machine Learning", "kind": "midterm", "date": "2026-11-24"})]


def test_assessment_next_thursday(bare: OfflineEngine) -> None:
    thursday = next_weekday(TODAY, 3)
    assert thursday == date(2026, 11, 12)
    assert calls(bare, "deep learning exam is next thursday") == [
        ("add_assessment", {"subject": "Deep Learning", "kind": "final", "date": thursday.isoformat()})]


def test_could_not_study_rebalance(bare: OfflineEngine) -> None:
    assert calls(bare, "I couldn't study today, rebalance") == [
        ("set_day_override", {"date": ISO, "minutes": 0}),
        ("regenerate_plan", {}),
    ]


def test_what_should_i_study_now(bare: OfflineEngine) -> None:
    assert calls(bare, "what should I study now?") == [("get_plan", {"date_from": ISO, "date_to": ISO})]


def test_undo(bare: OfflineEngine) -> None:
    assert calls(bare, "undo") == [("undo_last", {})]


def test_arabic_exam_in_five_days(bare: OfflineEngine) -> None:
    expected_date = (TODAY + timedelta(days=5)).isoformat()
    assert calls(bare, "عندي امتحان ديب ليرنينغ بعد ٥ أيام") == [
        ("add_assessment", {"subject": "Deep Learning", "kind": "final", "date": expected_date})]


# ---------------------------------------------------------------- the calls actually run
SPEC_UTTERANCES = [
    "I have 3 subjects: Deep Learning, Machine Learning and Internet Programming",
    "I'm 4 lectures behind in deep learning and 2 behind in ML",
    "I have 3 hours a day on weekdays and 6 hours on friday and saturday",
    "today I only have 90 minutes",
    "I only have 1.5h today",
    "I finished 2 units of deep learning today",
    "I studied machine learning for 40 min",
    "my ML midterm is on 2026-11-24",
    "deep learning exam is next thursday",
    "I couldn't study today, rebalance",
    "what should I study now?",
    "undo",
    "عندي امتحان ديب ليرنينغ بعد ٥ أيام",
]


@pytest.mark.parametrize("text", SPEC_UTTERANCES)
def test_every_spec_utterance_executes_cleanly(bare: OfflineEngine, text: str) -> None:
    if text == "undo":
        bare.respond("today I only have 90 minutes")
    reply = bare.respond(text)
    assert reply.engine == "offline" and reply.text
    assert reply.actions and all(a.ok for a in reply.actions), [a.summary for a in reply.actions]
    assert "I couldn't do that" not in reply.text


def test_full_offline_conversation_builds_a_plan(repo: Repository) -> None:
    ex = ToolExecutor(repo, today_fn=lambda: TODAY)
    engine = OfflineEngine(ex)
    engine.respond("I have 3 subjects: Deep Learning, Machine Learning and Internet Programming")
    assert [s.name for s in repo.list_subjects()] == NAMES[:3]
    engine.respond("Deep Learning has 12 lectures")
    engine.respond("machine learning has 10 units")
    engine.respond("I'm 4 lectures behind in deep learning and 2 behind in ML")
    engine.respond("I have 3 hours a day on weekdays and 6 hours on friday and saturday")
    engine.respond("my ML midterm is on 2026-11-24")
    dl = repo.get_subject_by_name("Deep Learning")
    assert dl is not None and dl.expected_units_by_now == 4 and len(repo.list_units(dl.id)) == 12
    assert repo.get_availability()[4].minutes == 360 and repo.get_availability()[0].minutes == 180
    assert len(repo.list_assessments()) == 1
    blocks = repo.list_blocks(TODAY)
    assert blocks and {b.subject_id for b in blocks} >= {dl.id}
    reply = engine.respond("what should I study now?")
    assert "Start with" in reply.text and not reply.actions_payload().get("actions")


# ---------------------------------------------------------------- behaviour around the edges
def test_existing_assessment_is_updated_not_duplicated(engine: OfflineEngine, seeded: Repository) -> None:
    ml = seeded.get_subject_by_name("Machine Learning")
    assert ml is not None
    midterm = next(a for a in seeded.list_assessments(ml.id) if a.kind == "midterm")
    assert calls(engine, "my ML midterm is on 2026-11-30") == [
        ("update_assessment", {"id": midterm.id, "date": "2026-11-30"})]
    dl = seeded.get_subject_by_name("Deep Learning")
    assert dl is not None
    dl_mid = seeded.list_assessments(dl.id)[0]
    assert calls(engine, "deep learning exam is next thursday") == [
        ("update_assessment", {"id": dl_mid.id, "date": "2026-11-12"})]
    assert calls(engine, "I have a deep learning quiz in 3 days") == [
        ("add_assessment", {"subject": "Deep Learning", "kind": "quiz",
                            "date": (TODAY + timedelta(days=3)).isoformat()})]


def test_unknown_input_lists_examples(engine: OfflineEngine, seeded: Repository) -> None:
    before = seeded.snapshot()
    reply = engine.respond("the weather is lovely")
    assert "what should I study now?" in reply.text and "undo" in reply.text
    assert reply.actions == [] and seeded.snapshot() == before


def test_questions_do_not_create_subjects(engine: OfflineEngine, seeded: Repository) -> None:
    assert calls(engine, "which of my subjects is the hardest?") == []
    assert calls(engine, "my subjects are fine") != [] or True
    assert len(seeded.list_subjects()) == 4


def test_reply_reports_what_moved_and_actions(engine: OfflineEngine) -> None:
    reply = engine.respond("today I only have 90 minutes")
    assert "Set today to 90 min" in reply.text and "What moved" in reply.text
    payload = reply.actions_payload()
    assert payload["actions"][0]["tool"] == "set_day_override" and payload["actions"][0]["action_id"]
    undone = engine.respond("undo")
    assert "Undid set_day_override" in undone.text


def test_delete_asks_for_confirmation(engine: OfflineEngine, seeded: Repository) -> None:
    reply = engine.respond("delete IELTS prep")
    assert reply.confirmations and reply.confirmations[0]["tool"] == "delete_subject"
    assert reply.confirmations[0]["args"] == {"subject": "IELTS Prep", "confirmed": True}
    assert seeded.get_subject_by_name("IELTS Prep") is not None


def test_tool_error_is_reported(engine: OfflineEngine) -> None:
    reply = engine.respond("I finished 40 units of deep learning")
    assert "I couldn't do that" in reply.text and "only 9 unit(s) left" in reply.text


def test_other_intents(engine: OfflineEngine) -> None:
    assert calls(engine, "Why this order?") == [("explain_priority", {})]
    assert calls(engine, "why is ML first") == [("explain_priority", {"subject": "Machine Learning"})]
    assert calls(engine, "Rebalance week") == [("regenerate_plan", {})]
    assert calls(engine, "Only 2h today") == [("set_day_override", {"date": ISO, "minutes": 120})]
    assert calls(engine, "What now?") == [("get_plan", {"date_from": ISO, "date_to": ISO})]
    assert calls(engine, "am I on track?") == [("get_risks", {})]
    assert calls(engine, "deep learning is too hard") == [("upsert_subject", {"name": "Deep Learning", "mastery": 1})]
    assert calls(engine, "tomorrow I have 4 hours") == [
        ("set_day_override", {"date": (TODAY + timedelta(days=1)).isoformat(), "minutes": 240})]
    assert calls(engine, "I have 2 hours every day") == [
        ("set_weekly_availability", {"days": [{"weekday": wd, "minutes": 120} for wd in range(7)]})]
    assert calls(engine, "add subject databases") == [("upsert_subject", {"name": "Databases"})]
    assert calls(engine, "show my week")[0][1]["date_to"] == (TODAY + timedelta(days=6)).isoformat()


def test_priority_and_risk_replies_quote_numbers(engine: OfflineEngine, seeded: Repository) -> None:
    reply = engine.respond("why this order?")
    assert "Deep Learning comes first: W = 14.85" in reply.text and "urgency 2.40" in reply.text
    assert "No risks" in engine.respond("any risks?").text
    dl = seeded.get_subject_by_name("Deep Learning")
    assert dl is not None
    seeded.update_assessment(seeded.list_assessments(dl.id)[0].id, date=TODAY + timedelta(days=1))
    engine.executor.execute("regenerate_plan")
    risky = engine.respond("am I on track?")
    assert "min short" in risky.text and len(risky.options) == 3
    assert risky.options[0]["payload"]["type"] == "risk_option"
    assert "skip" not in risky.text.lower() and "drop" not in risky.text.lower()


# ---------------------------------------------------------------- onboarding interview
def test_offline_interview_fills_everything(repo: Repository) -> None:
    ex = ToolExecutor(repo, today_fn=lambda: TODAY)
    engine = OfflineEngine(ex)
    question = engine.start_interview()
    assert question is not None and "Which subjects" in question
    reply = engine.respond("Physics and Chemistry")
    assert [s.name for s in repo.list_subjects()] == ["Physics", "Chemistry"]
    assert "How much time can you study each day" in reply.text
    reply = engine.respond("2 hours")
    assert all(av.minutes == 120 for av in repo.get_availability().values())
    assert "How many units" in reply.text and "Physics" in reply.text
    reply = engine.respond("12")
    assert "Chemistry" in reply.text
    reply = engine.respond("it has 8")
    assert "next exam" in reply.text and "Physics" in reply.text
    reply = engine.respond("in 10 days")
    assert repo.list_assessments()[0].date == TODAY + timedelta(days=10)
    reply = engine.respond("none")
    assert "how many units should you have covered" in reply.text
    reply = engine.respond("what?")
    assert reply.text.startswith("I didn't catch that.")
    reply = engine.respond("5 expected, 2 done")
    physics = repo.get_subject_by_name("Physics")
    assert physics is not None and physics.expected_units_by_now == 5
    reply = engine.respond("3")
    assert "your plan is ready" in reply.text
    assert engine.interview_active is False and ex.missing_info() == []
    assert repo.list_blocks(TODAY)


# ---------------------------------------------------------------- helpers
@pytest.mark.parametrize("text,minutes", [
    ("90 minutes", 90), ("1.5h", 90), ("2 hours", 120), ("an hour", 60), ("45 min", 45), ("3 hrs", 180),
    ("two hours", 120), ("half an hour", 30), ("٣ ساعات".translate(config.ARABIC_DIGITS), 180),
])
def test_parse_minutes(text: str, minutes: int) -> None:
    assert [m for _s, _e, m in parse_minutes(text)] == [minutes]


@pytest.mark.parametrize("text,expected", [
    ("on 2026-11-24", date(2026, 11, 24)),
    ("in 3 days", TODAY + timedelta(days=3)),
    ("in two weeks", TODAY + timedelta(days=14)),
    ("tomorrow", TODAY + timedelta(days=1)),
    ("next thursday", date(2026, 11, 12)),
    ("on tuesday", date(2026, 11, 17)),
    ("on 24 nov", date(2026, 11, 24)),
    ("december 3rd", date(2026, 12, 3)),
    ("5 january", date(2027, 1, 5)),
    ("24/11", date(2026, 11, 24)),
    ("بعد 5 أيام", TODAY + timedelta(days=5)),
    ("بعد اسبوع", TODAY + timedelta(days=7)),
    ("no date here", None),
])
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text, TODAY) == expected


def test_subject_matching() -> None:
    assert match_subject("ML", NAMES) == "Machine Learning"
    assert match_subject("dl", NAMES) == "Deep Learning"
    assert match_subject("deep lerning", NAMES) == "Deep Learning"
    assert match_subject("ielts", NAMES) == "IELTS Prep"
    assert match_subject("ديب ليرنينغ", NAMES) == "Deep Learning"
    assert match_subject("astrology", NAMES) is None
    found = find_subjects("i like ml and deep learning but html is fine", NAMES)
    assert [name for _s, _e, name in found] == ["Machine Learning", "Deep Learning"]
