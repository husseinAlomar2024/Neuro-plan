"""All constants for NeuroPlan. SPEC.md section 4 numbers live here and nowhere else."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "NeuroPlan"
KEYRING_SERVICE = "NeuroPlan"
DATA_DIR_NAME = "NeuroPlan"          # folder under %LOCALAPPDATA% used by the built exe
DB_FILENAME = "neuroplan.db"
LOG_FILENAME = "neuroplan.log"
ICON_FILENAME = "neuroplan.ico"
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
    "theme": "Midnight",
    "text_size": "Medium",
    "week_start": "sun",
    "plan_horizon_days": "14",
    "conversation_summary": "",
    "last_briefing_date": "",
}
PROVIDERS = ("anthropic", "openai_compat", "offline")
# ---------------------------------------------------------------- look and layout (UI preferences)
THEME_NAMES = ("Midnight", "Ivory & Gold", "Mist")
LEGACY_THEMES = {"Dark": "Midnight", "Light": "Ivory & Gold"}   # values stored by older versions
CHAT_TEXT_SIZES = {"Small": 13, "Medium": 15, "Large": 17}      # chat text px; every other size scales with it
TEXT_SIZE_NAMES = tuple(CHAT_TEXT_SIZES)
CHAT_FONT_SIZE = 15            # chat text at "Medium"
UI_FONT_SIZE = 13              # body text of the rest of the UI at "Medium"
CHAT_LINE_GAP = 3              # extra px between the lines of a chat bubble (before scaling)
DIVIDER_WIDTH = 6
COACH_DEFAULT_WIDTH = 380
COACH_MIN_WIDTH = 300
COLUMN_MIN_WIDTH = 280         # any column next to a divider
CENTRE_MIN_WIDTH = 400         # the coach divider never squeezes the centre view below this
SUBJECTS_LIST_DEFAULT_WIDTH = 420
PANE_MAX_WIDTH = 4000
RESIZE_DEBOUNCE_MS = 80        # re-wrap / re-fit once, this long after the last <Configure> of a resize
# divider position settings (unscaled px) and their defaults
PANE_SETTINGS = {"pane_coach_width": COACH_DEFAULT_WIDTH, "pane_subjects_list": SUBJECTS_LIST_DEFAULT_WIDTH}
# preferences the UI stores directly through set_ui_prefs (not undoable, no plan effect)
UI_PREFS: dict[str, tuple[str, ...] | None] = {"theme": THEME_NAMES, "text_size": TEXT_SIZE_NAMES,
                                               **{key: None for key in PANE_SETTINGS}}
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
    "theme": THEME_NAMES,
    "text_size": TEXT_SIZE_NAMES,
    "week_start": ("sun", "mon"),
    "plan_horizon_days": None,
}
TEXT_SETTINGS = ("base_url", "chat_model", "background_model")   # pasted values: quotes are stripped on save
PASTE_QUOTES = "\"'`“”‘’«»<>"
INVISIBLE_CHARS = "​‌‍⁠﻿"
# endpoint paths people paste after the base URL; the SDK appends its own
BASE_URL_ENDPOINT_SUFFIXES = ("/chat/completions", "/models")
# JSON-Schema keywords left out of the tool list for OpenAI-compatible servers (a precaution: not every
# server accepts strict-schema keywords); the executor validates arguments itself, so nothing is lost
OPENAI_SCHEMA_DROP = ("format", "additionalProperties")
ERROR_DETAIL_MAX_CHARS = 300  # how much of a provider's error message is shown and logged


def normalize_theme(value: str) -> str:
    """A stored theme name; values of older versions ("Dark"/"Light") map to their new theme."""
    text = str(value or "").strip()
    text = LEGACY_THEMES.get(text, text)
    return text if text in THEME_NAMES else THEME_NAMES[0]


def clean_pasted(value: str) -> str:
    """A pasted setting without surrounding whitespace, quotes and invisible characters."""
    text = str(value).translate({ord(ch): None for ch in INVISIBLE_CHARS})
    return text.strip().strip(PASTE_QUOTES).strip()


def clean_api_key(value: str) -> str:
    """A pasted API key: no quotes around it and no whitespace or line breaks anywhere."""
    return clean_pasted("".join(str(value).split()))


def clean_base_url(value: str) -> str:
    """A pasted base URL: no quotes or whitespace, no endpoint path at the end, always with a scheme."""
    url = clean_pasted("".join(str(value).split()))
    while url:
        stripped = url.rstrip("/")
        for suffix in BASE_URL_ENDPOINT_SUFFIXES:
            if stripped.lower().endswith(suffix):
                stripped = stripped[:-len(suffix)]
                break
        if stripped == url:
            break
        url = stripped
    if url and "://" not in url:
        local = url.lower().startswith("localhost") or url[0].isdigit() or url[0] == "["
        url = ("http://" if local else "https://") + url
    return url


DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
LLM_TIMEOUT_S = 30
LLM_MAX_ROUNDS = 6
LLM_MAX_TOKENS = 8000          # includes the model's (adaptive) thinking, replies themselves stay short
LLM_MAX_RETRIES = 1
LLM_EFFORT = "low"             # chat-sized work; keeps answers inside the 30 s timeout
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


def is_frozen() -> bool:
    """True when running as the built exe (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """Where the program itself lives: the exe's folder when frozen, else the project dir."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resolve_data_dir(frozen: bool, program_dir: Path, local_appdata: Optional[str], home: Path) -> Path:
    """Folder for neuroplan.db and neuroplan.log. The built exe uses %LOCALAPPDATA%\\NeuroPlan
    (``home``\\AppData\\Local\\NeuroPlan when the variable is missing); a run from source uses the project."""
    if not frozen:
        return program_dir
    base = Path(local_appdata) if local_appdata and local_appdata.strip() else home / "AppData" / "Local"
    return base / DATA_DIR_NAME


def app_dir() -> Path:
    """Directory that holds neuroplan.db and neuroplan.log; created if it does not exist yet."""
    path = resolve_data_dir(is_frozen(), install_dir(), os.environ.get("LOCALAPPDATA"), Path.home())
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_legacy_db(program_dir: Path, data_dir: Path) -> Optional[Path]:
    """Older builds kept neuroplan.db next to the exe. On the first run with the new location (no database
    there yet) copy that file over; the old one is left where it is. Returns the new path if a copy was made."""
    old, new = program_dir / DB_FILENAME, data_dir / DB_FILENAME
    if new.exists() or not old.is_file() or old.resolve() == new.resolve():
        return None
    data_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):                      # SQLite side files, if the old app left any
        side = old.with_name(old.name + suffix)
        if side.is_file():
            shutil.copy2(side, new.with_name(new.name + suffix))
    partial = new.with_name(new.name + ".copying")       # never leave a half-written neuroplan.db behind
    shutil.copy2(old, partial)
    os.replace(partial, new)
    return new


def icon_path() -> Path:
    """The window icon (bundled into the exe; ``assets`` in the project when running from source)."""
    return resource_dir().parent / "assets" / ICON_FILENAME


def resource_dir() -> Path:
    """Directory that holds bundled read-only resources (schema.sql)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "neuroplan"
    return Path(__file__).resolve().parent


def default_db_path() -> Path:
    return app_dir() / DB_FILENAME
