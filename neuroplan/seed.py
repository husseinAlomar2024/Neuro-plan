"""Demo data (SPEC section 3). Dates are relative to ``today``."""
from __future__ import annotations

from datetime import date, timedelta
from itertools import accumulate

from . import config
from .db.repository import Repository
from .scheduler.service import regenerate

# name, category, difficulty, mastery, units, expected, done, minutes/unit
SEED_SUBJECTS = [
    ("Deep Learning", config.HEAVY, 5, 2, 12, 7, 3, 75),
    ("Machine Learning", config.HEAVY, 4, 3, 12, 6, 4, 60),
    ("Internet Programming", config.CODING, 3, 4, 10, 5, 5, 50),
    ("IELTS Prep", config.MEMORY, 3, 3, 30, 8, 6, 45),
]
# subject, kind, title, days from today, unit_from, unit_to, weight
SEED_ASSESSMENTS = [
    ("Deep Learning", "midterm", "Deep Learning midterm", 10, 1, 6, 30),
    ("Machine Learning", "quiz", "Machine Learning quiz", 5, 3, 5, 10),
    ("Machine Learning", "midterm", "Machine Learning midterm", 14, 1, 7, 30),
    ("Internet Programming", "project", "Internet Programming project", 18, 1, 8, 25),
    ("IELTS Prep", "final", "IELTS exam", 30, 1, 30, 100),
]
SEED_DAYS_PER_UNIT = 2
WELCOME = ("Hi, I'm your study coach. I loaded a demo semester so you can look around: four subjects, "
           "their exams, and a 14-day plan. Tell me what's really on your plate (\"I have 3 subjects: …\", "
           "\"I'm 2 lectures behind in ML\", \"today I only have 90 minutes\") and I'll rebuild the plan "
           "with you. Ask \"what should I study now?\" any time.")


def review_stage_for_age(age_days: int) -> tuple[int, int] | None:
    """(stage_index, due offset from the done date) for a unit finished ``age_days`` ago,
    assuming every earlier review happened on time. None when all stages are behind us."""
    for stage, offset in enumerate(accumulate(config.REVIEW_STAGES_DAYS)):
        if offset >= age_days:
            return stage, offset
    return None


def seed_demo(repo: Repository, today: date, generate: bool = True) -> None:
    """Insert the demo data set into an empty database and build the plan."""
    with repo.transaction():
        for i, (name, cat, diff, mastery, units, expected, done, mpu) in enumerate(SEED_SUBJECTS):
            sid = repo.insert_subject(name, cat, diff, mastery, mpu, expected,
                                      config.SUBJECT_COLORS[i % len(config.SUBJECT_COLORS)])
            repo.set_total_units(sid, units)
            for unit in repo.list_units(sid):
                if unit.number > done:
                    continue
                age = (done - unit.number + 1) * SEED_DAYS_PER_UNIT
                done_date = today - timedelta(days=age)
                repo.update_unit(unit.id, status="done", minutes_logged=mpu, done_date=done_date)
                stage = review_stage_for_age(age)
                if stage is not None:
                    repo.insert_review(sid, unit.number, stage[0], done_date + timedelta(days=stage[1]))
        for name, kind, title, offset, u_from, u_to, weight in SEED_ASSESSMENTS:
            subject = repo.get_subject_by_name(name)
            assert subject is not None
            repo.insert_assessment(subject.id, kind, title, today + timedelta(days=offset), weight, u_from, u_to)
        for weekday in config.WORK_WEEKDAYS:
            repo.set_availability(weekday, 180, "18:00", "night")
        for weekday in config.WEEKEND_DAYS:
            repo.set_availability(weekday, 300, "10:00", "morning")
        repo.add_message("coach", WELCOME, proactive=True)
        repo.set_setting("seeded", "1")
        if generate:
            regenerate(repo, today)


def seed_if_first_launch(repo: Repository, today: date) -> bool:
    """Seed only on the very first launch (never again, even if the student deletes everything)."""
    if repo.get_setting("seeded") or repo.list_subjects():
        return False
    seed_demo(repo, today)
    return True
