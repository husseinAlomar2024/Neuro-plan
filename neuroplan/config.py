"""All constants for NeuroPlan. SPEC.md section 4 numbers live here and nowhere else."""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "NeuroPlan"
KEYRING_SERVICE = "NeuroPlan"
DB_FILENAME = "neuroplan.db"
LOG_FILENAME = "neuroplan.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3
SCHEMA_VERSION = 1

# ---------------------------------------------------------------- categories
HEAVY = "HEAVY"
CODING = "CODING"
MEMORY = "MEMORY"
REVIEW = "REVIEW"
CATEGORIES = (HEAVY, CODING, MEMORY)

CATCHUP = "CATCHUP"
PROGRESS = "PROGRESS"
PRACTICE = "PRACTICE"
BLOCK_TYPES = (CATCHUP, PROGRESS, REVIEW, PRACTICE)

ASSESSMENT_KINDS = ("quiz", "midterm", "final", "assignment", "project")
PEAK_WINDOWS = ("morning", "afternoon", "night")
RATINGS = ("again", "hard", "good", "easy")
BLOCK_STATUSES = ("pending", "done", "partial", "skipped")

# ---------------------------------------------------------------- scheduler (SPEC section 4)
REVIEW_STAGES_DAYS = [1, 3, 7, 16, 35]
REVIEW_BLOCK_MIN = 15          # one review block may cover up to 2 units of the same subject
REVIEW_UNITS_PER_BLOCK = 2
REVIEW_CAP_SHARE = 0.20
DAILY_CAP_SHARE = 0.60         # max share of a day for one subject
DAILY_CAP_SHARE_EXAM = 0.85    # when that subject has an assessment within EXAM_CLOSE_DAYS
EXAM_CLOSE_DAYS = 3
QUANTUM = {HEAVY: 50, CODING: 50, MEMORY: 25}
POMODORO = {HEAVY: "50/10", CODING: "50/10", MEMORY: "25/5", REVIEW: "25/5"}
BREAK_MIN = {"50/10": 10, "25/5": 5}
MAX_CONSECUTIVE_HEAVY_MIN = 100
NO_ASSESSMENT_DAYS = 60

# Priority formula coefficients (SPEC 4.1 step 3)
URGENCY_NUMERATOR = 14
BACKLOG_COEF = 0.45
DIFFICULTY_COEF = 0.15
DIFFICULTY_PIVOT = 3
MASTERY_COEF = 0.12
MASTERY_MAX = 5
SCOPE_BOOST_COEF = 0.5
SCOPE_BOOST_DAYS = 14

# Additions recorded in DECISIONS.md
STARVATION_GAP_DAYS = 3        # a subject with work appears at least once per this many days
FEASIBILITY_MAX_DAYS = 60      # how far the simulation looks ahead for the risk report
RISK_ROUND_MIN = 10            # "extra minutes/day" is rounded up to this step
MAX_DAY_MINUTES = 16 * 60

# Peak windows as [start_hour, end_hour) ; night wraps past midnight
PEAK_HOURS = {"morning": (5, 12), "afternoon": (12, 18), "night": (18, 5)}

# ---------------------------------------------------------------- defaults
DEFAULT_SETTINGS: dict[str, str] = {
    "provider": "offline",
    "base_url": "",
    "chat_model": "claude-sonnet-5-5",
    "background_model": "claude-haiku-5-5",
    "proactive_level": "full",
    "daily_llm_call_cap": "60",
    "end_of_day_time": "22:00",
    "theme": "Dark",
    "week_start": "sun",
    "plan_horizon_days": "14",
    "conversation_summary": "",
    "last_briefing_date": "",
}
PROVIDERS = ("anthropic", "openai_compat", "offline")
PROACTIVE_LEVELS = ("off", "light", "full")
# Settings the UI / tools may change, with their allowed values (None = free text / validated in code)
EDITABLE_SETTINGS: dict[str, tuple[str, ...] | None] = {
    "provider": PROVIDERS,
    "base_url": None,
    "chat_model": None,
    "background_model": None,
    "proactive_level": PROACTIVE_LEVELS,
    "daily_llm_call_cap": None,
    "end_of_day_time": None,
    "theme": ("Dark", "Light"),
    "week_start": ("sun", "mon"),
    "plan_horizon_days": None,
}
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
LLM_TIMEOUT_S = 30
LLM_MAX_ROUNDS = 6
LLM_MAX_TOKENS = 1024
HISTORY_WINDOW = 12
SUMMARY_TRIGGER = 30
SUMMARY_MAX_WORDS = 200
PLAN_CHANGED_DEBOUNCE_S = 3.0
REVIEWS_NUDGE_MIN = 3
UNDO_KEEP = 30                 # snapshots kept in action_log for undo

# Events the coach reacts to per proactive level
PROACTIVE_LIGHT = frozenset({
    "onboarding_needed", "daily_briefing", "risk_detected", "missed_yesterday",
    "block_skipped", "end_of_day",
})
PROACTIVE_FULL = PROACTIVE_LIGHT | frozenset({
    "plan_changed", "block_finished", "reviews_due", "break_started",
})

DEFAULT_SUBJECT = {
    "category": HEAVY, "difficulty": 3, "mastery": 3, "minutes_per_unit": 60,
}
SUBJECT_COLORS = ["#6C8EF5", "#F5A66C", "#5CC8A1", "#D97BD0", "#E5C45A", "#EF6F6C", "#5BC0DE", "#9B8CF2"]
DEFAULT_AVAILABILITY_MIN = 120
DEFAULT_START_TIME = "18:00"
DEFAULT_PEAK = "night"

# Weekdays use Python weekday(): 0=Mon ... 6=Sun. The study week here is Sun-Thu, weekend Fri-Sat.
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WORK_WEEKDAYS = [6, 0, 1, 2, 3]
WEEKEND_DAYS = [4, 5]

# ---------------------------------------------------------------- offline parser vocabulary
# Transliteration / nickname aliases: alias (lower-case) -> canonical subject name (lower-case)
SUBJECT_ALIASES: dict[str, str] = {
    "ديب ليرنينغ": "deep learning",
    "ديب ليرننغ": "deep learning",
    "ديب ليرنينج": "deep learning",
    "التعلم العميق": "deep learning",
    "تعلم عميق": "deep learning",
    "ماشين ليرنينغ": "machine learning",
    "ماشين ليرننغ": "machine learning",
    "ماشين ليرنينج": "machine learning",
    "تعلم الآلة": "machine learning",
    "تعلم الاله": "machine learning",
    "انترنت بروغرامينغ": "internet programming",
    "برمجة الانترنت": "internet programming",
    "برمجة الإنترنت": "internet programming",
    "ايلتس": "ielts prep",
    "آيلتس": "ielts prep",
    "الايلتس": "ielts prep",
    "ielts": "ielts prep",
    "web": "internet programming",
    "web programming": "internet programming",
}
ASSESSMENT_WORDS: dict[str, str] = {
    "quiz": "quiz", "كويز": "quiz", "اختبار قصير": "quiz",
    "midterm": "midterm", "mid-term": "midterm", "mid term": "midterm", "ميدتيرم": "midterm",
    "ميد": "midterm", "نصفي": "midterm",
    "final": "final", "exam": "final", "test": "final", "فاينل": "final", "نهائي": "final",
    "امتحان": "final", "اختبار": "final",
    "assignment": "assignment", "homework": "assignment", "واجب": "assignment",
    "project": "project", "مشروع": "project",
}
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "a": 1, "an": 1, "half": 0.5,
}
SKIP_REASONS = ["No time", "Too hard", "Didn't feel like it", "Sick"]
QUICK_CHIPS = ["What now?", "Only 2h today", "I'm behind in…", "Rebalance week", "Why this order?"]
BREAK_TIPS = [
    "Break time: stand up, stretch, and look at something far away.",
    "Break: drink some water and stay off your phone — your brain is consolidating.",
    "Break: take a short walk; the next block will feel lighter.",
    "Break: breathe slowly for a minute, then come back fresh.",
]


def app_dir() -> Path:
    """Directory that holds neuroplan.db: next to the exe when frozen, else the project dir."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory that holds bundled read-only resources (schema.sql)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "neuroplan"
    return Path(__file__).resolve().parent


def default_db_path() -> Path:
    return app_dir() / DB_FILENAME
