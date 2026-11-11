"""Rule-based coach: a regex/keyword intent parser that emits the same tool calls as the LLM,
then answers with template messages. Keeps the app fully usable with no API key."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Iterator, Optional

from dateutil import parser as date_parser

from .. import config
from ..tools.executor import ToolExecutor
from ..tools.matching import find_subjects, match_subject, normalize_keep_length
from ..tools.registry import TOOLS
from .types import ActionRecord, EngineReply, ToolCall

_B = r"(?<![^\W_])"          # unicode-aware word start
_E = r"(?![^\W_])"           # unicode-aware word end
_NUM_WORDS = "|".join(sorted(config.NUMBER_WORDS, key=len, reverse=True))
_NUM = rf"(\d+(?:[.,]\d+)?|{_B}(?:{_NUM_WORDS}){_E})"
_DURATION_RE = re.compile(
    rf"{_NUM}\s*(?:and a half\s*)?(hours?|hrs?|hr|h|minutes?|mins?|min|m|ساعات|ساعة|ساعه|دقائق|دقيقة|دقيقه){_E}")
_HALF_HOUR_RE = re.compile(r"half an hour|نص ساعة|نصف ساعة")
_UNIT_WORD = r"(?:units?|lectures?|chapters?|lessons?|topics?|weeks?|محاضرات|محاضرة|وحدات|وحدة)"
_DAY_NAMES = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
              "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
              "sunday": 6, "sun": 6}
_DAY_RE = re.compile(rf"{_B}(" + "|".join(sorted(_DAY_NAMES, key=len, reverse=True)) + rf")s?{_E}")
_GROUP_RE = re.compile(rf"{_B}(weekdays?|week days?|weekends?|every day|everyday|each day|daily|all week){_E}")
_PER_DAY_RE = re.compile(rf"{_B}(a day|per day|each day|every day|daily){_E}")
_TODAY_RE = re.compile(rf"{_B}(today|tonight|this evening|اليوم|الليلة){_E}")
_TOMORROW_RE = re.compile(rf"{_B}(tomorrow|tmrw|بكرة|بكره|غدا|غداً){_E}")
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_GENERIC_EXAM = {"exam", "test", "امتحان", "اختبار"}
_AVAILABILITY_CUE_RE = re.compile(rf"{_B}(have|got|only|free|available|left|can|capacity|limit(ed)?){_E}|عندي|فقط")
# Clause boundaries. Hard: after sentence punctuation or at a line break. Soft: a comma, "and"/"also",
# or the start of a new statement ("I ...", "today ...", "my ...").
_SENTENCE_RE = re.compile(r"(?<=[.!?;؟؛])\s+|\s*\n+\s*")
_SOFT_BOUNDARY_RE = re.compile(
    rf",(?!\d)|،|{_B}(?:and|also){_E}|(?<=\S)\s+(?={_B}(?:i|today|tonight|tomorrow|my){_E})")
_MAX_FRAGMENTS = 12
_Span = tuple[int, int]
_NONE_RE = re.compile(rf"^\s*(none|no|nope|nothing|not yet|skip|n/a|don'?t know|idk|لا|مافي|ما في|لا يوجد)\b")

HELP = ("I'm running offline, so I understand simple commands. Try:\n"
        "• \"what should I study now?\"\n"
        "• \"today I only have 90 minutes\"\n"
        "• \"I'm 2 lectures behind in ML\"\n"
        "• \"I finished 2 units of Deep Learning\" / \"I studied ML for 40 min\"\n"
        "• \"my ML midterm is on 2026-11-24\"\n"
        "• \"I have 3 hours a day on weekdays and 6 hours on friday and saturday\"\n"
        "• \"I couldn't study today, rebalance\" · \"why this order?\" · \"undo\"\n"
        "• \"archive IELTS\" · \"delete subject IELTS\"\n"
        "You can put several of these in one message.")


def to_number(token: str) -> float:
    token = token.strip().lower()
    if token in config.NUMBER_WORDS:
        return float(config.NUMBER_WORDS[token])
    return float(token.replace(",", "."))


def parse_minutes(text: str) -> list[tuple[int, int, int]]:
    """All durations in ``text`` as (start, end, minutes)."""
    out: list[tuple[int, int, int]] = [(m.start(), m.end(), 30) for m in _HALF_HOUR_RE.finditer(text)]
    halves = list(out)
    for m in _DURATION_RE.finditer(text):
        if any(s < m.end() and m.start() < e for s, e, _ in halves):
            continue
        value = to_number(m.group(1))
        unit = m.group(2)
        hours = unit.startswith("h") or unit.startswith("ساع")
        if "and a half" in m.group(0):
            value += 0.5
        out.append((m.start(), m.end(), int(round(value * 60 if hours else value))))
    return sorted(out)


def next_weekday(today: date, weekday: int) -> date:
    """The next date strictly after today that falls on ``weekday``."""
    ahead = (weekday - today.weekday() - 1) % 7 + 1
    return today + timedelta(days=ahead)


def parse_date(text: str, today: date) -> Optional[date]:
    """Find a date expression in normalised (lower-case, ASCII-digit) text."""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(rf"{_B}in\s+{_NUM}\s*(days?|weeks?|months?){_E}", text)
    if m:
        n = to_number(m.group(1))
        factor = 7 if m.group(2).startswith("week") else 30 if m.group(2).startswith("month") else 1
        return today + timedelta(days=int(round(n * factor)))
    if re.search(r"بعد\s+(يومين)", text):
        return today + timedelta(days=2)
    if re.search(r"بعد\s+(اسبوعين|أسبوعين)", text):
        return today + timedelta(days=14)
    m = re.search(r"بعد\s+(\d+)\s*(يوم|أيام|ايام|اسبوع|أسبوع|اسابيع|أسابيع)", text)
    if m:
        factor = 7 if "سبوع" in m.group(2) or "سابيع" in m.group(2) else 1
        return today + timedelta(days=int(m.group(1)) * factor)
    if re.search(r"بعد\s+(اسبوع|أسبوع)", text):
        return today + timedelta(days=7)
    if re.search(rf"{_B}day after tomorrow{_E}|بعد بكرة|بعد غد", text):
        return today + timedelta(days=2)
    if _TOMORROW_RE.search(text):
        return today + timedelta(days=1)
    if re.search(rf"{_B}next week{_E}", text):
        return today + timedelta(days=7)
    m = _DAY_RE.search(text)
    if m:
        return next_weekday(today, _DAY_NAMES[m.group(1)])
    m = re.search(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTHS})|({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?", text)
    if m:
        try:
            parsed = date_parser.parse(m.group(0).replace(" of ", " "), default=_as_datetime(today)).date()
        except (ValueError, OverflowError):
            return None
        if parsed < today:
            parsed = parsed.replace(year=parsed.year + 1)
        return parsed
    m = re.search(rf"{_B}(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{2,4}}))?{_E}", text)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        year += 2000 if year < 100 else 0
        try:
            parsed = date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        if parsed < today and not m.group(3):
            parsed = parsed.replace(year=year + 1)
        return parsed
    if _TODAY_RE.search(text):
        return today
    return None


def _as_datetime(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def find_kind(text: str) -> Optional[tuple[str, str]]:
    """(assessment kind, the word that matched)."""
    for word in sorted(config.ASSESSMENT_WORDS, key=len, reverse=True):
        if re.search(_B + re.escape(word) + r"s?" + _E, text):
            return config.ASSESSMENT_WORDS[word], word
    return None


def clean_subject_name(fragment: str) -> str:
    name = re.sub(r"\s+", " ", fragment).strip(" .,:;!?\"'()[]")
    name = re.sub(r"^(and|&|و)\s+", "", name, flags=re.IGNORECASE).strip()
    if name and name == name.lower():
        name = " ".join(w.upper() if len(w) <= 2 and w.isalpha() and w not in ("of", "to", "in", "on")
                        else w.capitalize() for w in name.split())
    return name


@dataclass
class _Ctx:
    text: str                                  # normalised, same length as raw
    raw: str
    today: date
    subjects: list[dict[str, Any]]
    mentions: list[tuple[int, int, str]]

    def subject(self, name: str) -> dict[str, Any]:
        return next(s for s in self.subjects if s["name"] == name)

    def first_after(self, pos: int, before: Optional[int] = None) -> Optional[str]:
        for start, _end, name in self.mentions:
            if start >= pos and (before is None or start < before):
                return name
        return None


class OfflineEngine:
    def __init__(self, executor: ToolExecutor) -> None:
        self.executor = executor
        self.pending: Optional[dict[str, Any]] = None     # onboarding question we are waiting on
        self.interview_active = False

    # ------------------------------------------------------------ parsing
    def _context(self, text: str) -> _Ctx:
        subjects = self.executor.execute("list_subjects").data["subjects"]
        norm = normalize_keep_length(text)
        return _Ctx(norm, text, self.executor.today(), subjects,
                    find_subjects(norm, [s["name"] for s in subjects]))

    def parse(self, text: str) -> list[ToolCall]:
        """Map an utterance to tool calls (empty list when nothing matches). A message may hold
        several intents: every recognised clause contributes its calls, in text order."""
        return list(self._calls_for(self.clauses(text), fresh=False))

    def clauses(self, text: str) -> list[str]:
        """The parts of ``text`` that each map to an intent, in text order."""
        ctx = self._context(text)
        spans: list[_Span] = []
        for sentence in self._split(ctx, _SENTENCE_RE, (0, len(ctx.text))):
            spans.extend(self._best_grouping(ctx, self._split(ctx, _SOFT_BOUNDARY_RE, sentence)))
        if not spans and self._parse_clause(ctx):
            spans = [(0, len(ctx.text))]              # an intent that only shows across sentences
        spans = self._join_availability(ctx, spans)
        return [ctx.raw[start:end] for start, end in spans]

    @staticmethod
    def _split(ctx: _Ctx, boundary: "re.Pattern[str]", span: _Span) -> list[_Span]:
        """Cut ``span`` at every boundary match that is not inside a subject name; trimmed, non-empty."""
        out: list[_Span] = []
        start = span[0]
        cuts = [(m.start(), m.end()) for m in boundary.finditer(ctx.text, span[0], span[1])
                if not any(s < m.end() and m.start() < e for s, e, _ in ctx.mentions)]
        for cut_start, cut_end in cuts + [(span[1], span[1])]:
            piece = ctx.text[start:cut_start]
            if piece.strip():
                lead = len(piece) - len(piece.lstrip())
                out.append((start + lead, start + len(piece.rstrip())))
            start = cut_end
        return out

    def _best_grouping(self, ctx: _Ctx, fragments: list[_Span]) -> list[_Span]:
        """Group neighbouring fragments into clauses so that as little text as possible stays
        unrecognised and, among those groupings, as many clauses as possible carry an intent.
        ("today | I only have 90 minutes | I'm 3 lectures behind in ML" becomes two clauses; a list like
        "subjects: A, B and C" stays whole because its pieces mean nothing on their own.)"""
        if len(fragments) > _MAX_FRAGMENTS:
            fragments = fragments[:_MAX_FRAGMENTS - 1] + [(fragments[_MAX_FRAGMENTS - 1][0], fragments[-1][1])]
        known: dict[_Span, bool] = {}

        def recognised(i: int, j: int) -> bool:
            span = (fragments[i][0], fragments[j - 1][1])
            if span not in known:
                known[span] = bool(self._parse_clause(self._sub_context(ctx, span)))
            return known[span]

        def weak(i: int) -> int:
            """1 when fragment i starts at a bare "I"/"today"/"my" boundary (no comma or "and" before it)."""
            return int(i > 0 and not ctx.text[fragments[i - 1][1]:fragments[i][0]].strip())

        # (unrecognised fragments, -clauses, cuts at weak boundaries, back pointer): on a tie a trailing
        # "today"/"tomorrow" stays with the clause before it ("I couldn't study tomorrow, rebalance").
        best: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)]
        for j in range(1, len(fragments) + 1):
            options: list[tuple[int, int, int, int]] = []
            for i in range(j):
                unknown, negative_clauses, weak_cuts, _back = best[i]
                if recognised(i, j):
                    options.append((unknown, negative_clauses - 1, weak_cuts + weak(i), i))
                else:
                    options.append((unknown + j - i, negative_clauses, weak_cuts + weak(i), i))
            best.append(min(options))
        spans: list[_Span] = []
        j = len(fragments)
        while j > 0:
            i = best[j][3]
            if recognised(i, j):
                spans.append((fragments[i][0], fragments[j - 1][1]))
            j = i
        return spans[::-1]

    def _join_availability(self, ctx: _Ctx, spans: list[_Span]) -> list[_Span]:
        """'3 hours on weekdays and 6 hours on friday' is one weekly schedule, not two."""
        def weekly(span: _Span) -> bool:
            calls = self._parse_clause(self._sub_context(ctx, span))
            return len(calls) == 1 and calls[0].name == "set_weekly_availability"

        out: list[_Span] = []
        for span in spans:
            if out and weekly(out[-1]) and weekly(span) and weekly((out[-1][0], span[1])):
                out[-1] = (out[-1][0], span[1])
            else:
                out.append(span)
        return out

    @staticmethod
    def _sub_context(ctx: _Ctx, span: _Span) -> _Ctx:
        text = ctx.text[span[0]:span[1]]
        return _Ctx(text, ctx.raw[span[0]:span[1]], ctx.today, ctx.subjects,
                    find_subjects(text, [s["name"] for s in ctx.subjects]))

    def _calls_for(self, clauses: list[str], fresh: bool = True) -> Iterator[ToolCall]:
        """The calls of every clause, without repeats. With ``fresh`` each clause is read against the
        state left by the calls before it (the caller executes them as they are yielded)."""
        seen: list[ToolCall] = []
        subjects: Optional[list[dict[str, Any]]] = None
        for clause in clauses:
            if fresh or subjects is None:
                ctx = self._context(clause)
                subjects = ctx.subjects
            else:
                norm = normalize_keep_length(clause)
                ctx = _Ctx(norm, clause, self.executor.today(), subjects,
                           find_subjects(norm, [s["name"] for s in subjects]))
            for call in self._parse_clause(ctx):
                if call not in seen:
                    seen.append(call)
                    yield call

    def _parse_clause(self, ctx: _Ctx) -> list[ToolCall]:
        """The first intent that matches one clause."""
        intents: list[Callable[[_Ctx], Optional[list[ToolCall]]]] = [
            self._undo, self._subject_list, self._behind, self._finished_units, self._studied_minutes,
            self._archive_subject, self._delete_subject, self._total_units, self._assessment,
            self._could_not_study, self._day_override, self._weekly_availability, self._what_now, self._why,
            self._risks, self._too_hard, self._rebalance,
        ]
        for intent in intents:
            calls = intent(ctx)
            if calls:
                return calls
        return []

    def _undo(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"^\s*(please\s+)?(undo|revert)\b|{_B}(undo that|undo the last|take that back){_E}|تراجع", c.text):
            return [ToolCall("undo_last", {})]
        return None

    def _subject_list(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if c.text.rstrip().endswith("?"):
            return None
        noun = r"(?:subjects?|courses?|classes|modules?)"
        m = (re.search(rf"{_B}{noun}\s*[:\-–]\s*(.+)$", c.text)
             or re.search(rf"{_B}(?:my|the)\s+{noun}\s+(?:are|is)\s+(.+)$", c.text)
             or re.search(rf"^\s*(?:please\s+)?add\s+(?:a\s+|the\s+)?(?:new\s+)?{noun}\s+"
                          rf"(?:called\s+|named\s+)?(.+)$", c.text))
        if not m:
            return None
        return self._subjects_from_list(c, c.raw[m.start(1):m.end(1)]) or None

    def _subjects_from_list(self, c: _Ctx, fragment: str) -> list[ToolCall]:
        known = [s["name"] for s in c.subjects]
        calls: list[ToolCall] = []
        seen: set[str] = set()
        for part in re.split(r",|;|\n|\s+and\s+|\s*&\s*|\s+و\s*", fragment, flags=re.IGNORECASE):
            name = clean_subject_name(part)
            if not name or len(name) > 60:
                continue
            name = match_subject(name, known) or name
            if name.lower() not in seen:
                seen.add(name.lower())
                calls.append(ToolCall("upsert_subject", {"name": name}))
        return calls

    def _behind(self, c: _Ctx) -> Optional[list[ToolCall]]:
        matches = list(re.finditer(rf"{_NUM}\s*(?:{_UNIT_WORD}\s+)?behind{_E}", c.text))
        calls: list[ToolCall] = []
        for i, m in enumerate(matches):
            limit = matches[i + 1].start() if i + 1 < len(matches) else None
            name = c.first_after(m.end(), limit)
            if name is None:
                continue
            s = c.subject(name)
            expected = s["done_units"] + int(to_number(m.group(1)))
            if s["total_units"]:
                expected = min(expected, s["total_units"])
            calls.append(ToolCall("set_progress", {"subject": name, "expected_units_by_now": expected}))
        return calls or None

    def _finished_units(self, c: _Ctx) -> Optional[list[ToolCall]]:
        m = re.search(rf"{_B}(?:finished|completed|done with|did|covered|خلصت|أنهيت|انهيت)\s+{_NUM}\s*{_UNIT_WORD}", c.text)
        if not m or not c.mentions:
            return None
        name = c.first_after(m.end()) or c.mentions[0][2]
        return [ToolCall("log_study", {"subject": name, "units": max(1, int(to_number(m.group(1))))})]

    def _studied_minutes(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if not re.search(rf"{_B}(studied|worked on|practi[cs]ed|revised|spent|did|درست|ذاكرت){_E}", c.text):
            return None
        durations = parse_minutes(c.text)
        if not durations or not c.mentions:
            return None
        minutes = durations[0][2]
        if not 1 <= minutes <= 600:
            return None
        return [ToolCall("log_study", {"subject": c.mentions[0][2], "minutes": minutes})]

    def _archive_subject(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"^\s*(?:please\s+)?archive{_E}", c.text) and c.mentions:
            return [ToolCall("upsert_subject", {"name": c.mentions[0][2], "active": False})]
        return None

    def _delete_subject(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"^\s*(?:please\s+)?(delete|remove)\b", c.text) and c.mentions:
            return [ToolCall("delete_subject", {"subject": c.mentions[0][2]})]
        return None

    def _total_units(self, c: _Ctx) -> Optional[list[ToolCall]]:
        m = re.search(rf"{_B}has\s+(\d+)\s*{_UNIT_WORD}", c.text)
        if m and c.mentions:
            return [ToolCall("upsert_subject", {"name": c.mentions[0][2], "total_units": int(m.group(1))})]
        return None

    def _assessment(self, c: _Ctx) -> Optional[list[ToolCall]]:
        kind = find_kind(c.text)
        if kind is None or not c.mentions:
            return None
        when = parse_date(c.text, c.today)
        if when is None:
            return None
        return [self._assessment_call(c, c.mentions[0][2], kind[0], kind[1], when)]

    def _assessment_call(self, c: _Ctx, name: str, kind: str, word: str, when: date) -> ToolCall:
        upcoming = [a for a in c.subject(name)["assessments"] if a["days_until"] >= 0]
        if word in _GENERIC_EXAM:
            exams = [a for a in upcoming if a["kind"] in ("midterm", "final")]
            same = exams if len(exams) == 1 else []
        else:
            same = [a for a in upcoming if a["kind"] == kind]
        if same:
            return ToolCall("update_assessment", {"id": same[0]["id"], "date": when.isoformat()})
        return ToolCall("add_assessment", {"subject": name, "kind": kind, "date": when.isoformat()})

    def _could_not_study(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"(couldn'?t|could not|can'?t|cannot|didn'?t|did not|won'?t be able to|unable to)\s+(study|work)"
                     rf"|{_B}no time (today|tonight)|ما قدرت أدرس|ما درست", c.text):
            day = c.today + timedelta(days=1) if _TOMORROW_RE.search(c.text) else c.today
            return [ToolCall("set_day_override", {"date": day.isoformat(), "minutes": 0}),
                    ToolCall("regenerate_plan", {})]
        return None

    def _day_override(self, c: _Ctx) -> Optional[list[ToolCall]]:
        tomorrow = _TOMORROW_RE.search(c.text)
        if not (tomorrow or _TODAY_RE.search(c.text)):
            return None
        durations = parse_minutes(c.text)
        if not durations:
            return None
        if c.mentions and not _AVAILABILITY_CUE_RE.search(c.text):
            return None                       # "DL for 2 hours today" is about a subject, not the day
        day = c.today + timedelta(days=1) if tomorrow else c.today
        minutes = min(config.MAX_DAY_MINUTES, durations[0][2])
        return [ToolCall("set_day_override", {"date": day.isoformat(), "minutes": minutes})]

    def _weekly_availability(self, c: _Ctx) -> Optional[list[ToolCall]]:
        durations = parse_minutes(c.text)
        if not durations:
            return None
        named = [(m.start(), [_DAY_NAMES[m.group(1)]]) for m in _DAY_RE.finditer(c.text)]
        explicit = {wd for _pos, days in named for wd in days}
        groups: list[tuple[int, list[int]]] = list(named)
        for m in _GROUP_RE.finditer(c.text):
            word = m.group(1)
            if word.startswith("weekend"):
                days = [wd for wd in config.WEEKEND_DAYS if wd not in explicit]
            elif word.startswith("week"):
                days = [wd for wd in config.WORK_WEEKDAYS if wd not in explicit]
                if word == "all week":
                    days = [wd for wd in range(7) if wd not in explicit]
            else:
                continue                      # "every day"/"daily" are handled as per-day wording below
            groups.append((m.start(), days))
        minutes_by_day: dict[int, int] = {}
        if groups:
            for pos, days in sorted(groups):
                before = [d for d in durations if d[0] < pos]
                chosen = before[-1] if before else durations[0]
                for wd in days:
                    minutes_by_day[wd] = min(config.MAX_DAY_MINUTES, chosen[2])
        elif _PER_DAY_RE.search(c.text) or re.search(rf"{_B}(every day|everyday|daily){_E}", c.text):
            minutes_by_day = {wd: min(config.MAX_DAY_MINUTES, durations[0][2]) for wd in range(7)}
        if not minutes_by_day:
            return None
        days_arg = [{"weekday": wd, "minutes": minutes_by_day[wd]} for wd in sorted(minutes_by_day)]
        return [ToolCall("set_weekly_availability", {"days": days_arg})]

    def _what_now(self, c: _Ctx) -> Optional[list[ToolCall]]:
        t = c.text
        if re.search(rf"what (should|do|can|shall) i (study|do|work on)|what now|what'?s next|what is next|"
                     rf"where (do|should) i start|{_B}(today'?s|my) plan{_E}|plan for (today|tomorrow|the week|this week)|"
                     rf"show (me )?(the |my )?(plan|week|schedule)|ماذا أدرس|ماذا ادرس|شو أدرس|شو ادرس|ايش ادرس", t):
            start = c.today
            end = c.today
            if _TOMORROW_RE.search(t):
                start = end = c.today + timedelta(days=1)
            elif re.search(rf"{_B}(week|schedule){_E}", t):
                end = c.today + timedelta(days=6)
            return [ToolCall("get_plan", {"date_from": start.isoformat(), "date_to": end.isoformat()})]
        return None

    def _why(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"{_B}why{_E}|{_B}explain{_E}|{_B}priorit(y|ies|ise|ize){_E}|ليش|لماذا", c.text):
            args = {"subject": c.mentions[0][2]} if c.mentions else {}
            return [ToolCall("explain_priority", args)]
        return None

    def _risks(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"{_B}risks?{_E}|at risk|on track|will i (make|be ready|finish)|am i (ready|ok|okay|fine)", c.text):
            return [ToolCall("get_risks", {})]
        return None

    def _too_hard(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if c.mentions and re.search(rf"too (hard|difficult)|struggling|don'?t understand|صعب", c.text):
            s = c.subject(c.mentions[0][2])
            return [ToolCall("upsert_subject", {"name": s["name"], "mastery": max(1, s["mastery"] - 1)})]
        m = re.search(rf"mastery\D{{0,20}}(\d)", c.text)
        if m and c.mentions and 1 <= int(m.group(1)) <= 5:
            return [ToolCall("upsert_subject", {"name": c.mentions[0][2], "mastery": int(m.group(1))})]
        return None

    def _rebalance(self, c: _Ctx) -> Optional[list[ToolCall]]:
        if re.search(rf"{_B}(rebalance|re-balance|replan|re-plan|regenerate|reschedule|rebuild|refresh)", c.text):
            return [ToolCall("regenerate_plan", {})]
        return None

    # ------------------------------------------------------------ onboarding interview
    def start_interview(self) -> Optional[str]:
        """Begin (or continue) the intake interview. Returns the next question, or None when complete."""
        self.interview_active = True
        return self.next_question()

    def next_question(self) -> Optional[str]:
        missing = self.executor.missing_info()
        if not missing:
            self.pending = None
            self.interview_active = False
            return None
        self.pending = missing[0]
        subject, field_name = self.pending["subject"], self.pending["field"]
        if field_name == "subjects":
            return ("Let's build your plan. Which subjects are you taking this term? "
                    "(for example: \"I have 3 subjects: Deep Learning, Machine Learning and Databases\")")
        if field_name == "availability":
            return ("How much time can you study each day? (for example: \"3 hours a day on weekdays "
                    "and 6 hours on friday and saturday\", or just \"2 hours\")")
        if field_name == "units":
            return f"How many units (lectures/chapters) does {subject} have in total?"
        if field_name == "assessment":
            return (f"When is your next exam, quiz or deadline for {subject}? "
                    f"(a date like 2026-11-24, \"in 10 days\", or \"none\")")
        return (f"For {subject}: how many units should you have covered by now, and how many have you "
                f"actually finished? (for example \"7 expected, 3 done\")")

    def _answer_pending(self, text: str) -> list[ToolCall]:
        """Interpret a short reply to the question we asked last."""
        if self.pending is None:
            return []
        c = self._context(text)
        subject, field_name = self.pending["subject"], self.pending["field"]
        numbers = [int(n) for n in re.findall(r"(?<![\d-])\d{1,3}(?![\d-])", c.text)]
        if field_name == "subjects":
            return self._subjects_from_list(c, c.raw)
        if field_name == "availability":
            durations = parse_minutes(c.text)
            if not durations:
                return []
            minutes = min(config.MAX_DAY_MINUTES, durations[0][2])
            return [ToolCall("set_weekly_availability",
                             {"days": [{"weekday": wd, "minutes": minutes} for wd in range(7)]})]
        if _NONE_RE.search(c.text) and field_name in ("assessment", "units"):
            return [ToolCall("dismiss_missing_info", {"subject": subject, "field": field_name})]
        if field_name == "units" and numbers:
            return [ToolCall("upsert_subject", {"name": subject, "total_units": numbers[0]})]
        if field_name == "assessment":
            when = parse_date(c.text, c.today)
            if when is not None:
                kind = find_kind(c.text) or ("final", "exam")
                return [self._assessment_call(c, subject, kind[0], kind[1], when)]
        if field_name == "expected":
            if _NONE_RE.search(c.text):
                return [ToolCall("set_progress", {"subject": subject, "expected_units_by_now": 0})]
            if numbers:
                args: dict[str, Any] = {"subject": subject, "expected_units_by_now": numbers[0]}
                if len(numbers) > 1:
                    args["done_units"] = numbers[1]
                return [ToolCall("set_progress", args)]
        return []

    # ------------------------------------------------------------ responding
    def respond(self, text: str, source: str = "chat_offline") -> EngineReply:
        clauses = self.clauses(text)
        calls: Iterable[ToolCall] = self._calls_for(clauses)
        if not clauses:
            calls = self._answer_pending(text) if self.pending is not None else []
            if not calls:
                reply = EngineReply(self._unknown(text), engine="offline")
                return self._with_next_question(reply, repeat=True)
        reply = self.run_calls(calls, source)
        return self._with_next_question(reply)

    def _with_next_question(self, reply: EngineReply, repeat: bool = False) -> EngineReply:
        if not self.interview_active:
            return reply
        question = self.next_question()
        if question is None:
            reply.text += "\n\nThat's everything I need — your plan is ready. Ask \"what should I study now?\""
        elif repeat:
            reply.text = f"I didn't catch that. {question}"
        else:
            reply.text += f"\n\n{question}"
        return reply

    def run_calls(self, calls: Iterable[ToolCall], source: str = "chat_offline") -> EngineReply:
        reply = EngineReply("", engine="offline")
        parts: list[str] = []
        errors: list[str] = []
        moved: list[str] = []
        for call in calls:
            result = self.executor.execute(call.name, call.args, source)
            spec = TOOLS[call.name]
            reply.actions.append(ActionRecord.from_result(call, result, spec.write))
            if not result.ok:
                errors.append(result.summary)
                continue
            if result.needs_confirmation:
                reply.confirmations.append({"tool": call.name, "args": result.data["args"],
                                            "preview": result.preview})
                reply.actions.pop()
                parts.append(result.preview)
                continue
            if result.plan_change and result.plan_change not in moved:
                moved.append(result.plan_change)
            if spec.write:
                parts.append(result.summary + ".")
            else:
                parts.append(self._describe_read(call, result.data, reply))
        text = " ".join(p for p in parts if p)
        if moved and any(a.write and a.ok for a in reply.actions):
            text += f"\nWhat moved — {'; '.join(moved)}."
        if errors:
            text += ("\n" if text else "") + "I couldn't do that: " + " ".join(errors)
        reply.text = text.strip() or "Done."
        return reply

    def _describe_read(self, call: ToolCall, data: Any, reply: EngineReply) -> str:
        if call.name == "get_plan":
            return self.describe_plan(data)
        if call.name == "explain_priority":
            return self.describe_priorities(data)
        if call.name == "get_risks":
            return self.describe_risks(data["risks"], reply)
        return ""

    def describe_plan(self, data: dict[str, Any]) -> str:
        days = data["days"]
        if len(days) == 1:
            day = days[0]
            pending = [b for b in day["blocks"] if b["status"] == "pending"]
            label = "today" if day["date"] == self.executor.today().isoformat() else day["weekday"]
            if not pending:
                done = sum(1 for b in day["blocks"] if b["status"] in ("done", "partial"))
                return (f"Nothing left for {label} — {done} block(s) done. Rest, or tell me if you have "
                        f"extra time.") if done else f"Nothing is planned for {label}."
            first = pending[0]
            lines = [f"Start with {first['subject']} at {first['start']} ({first['min']} min, "
                     f"{first['pomodoro']}): {first['task']}"]
            if len(pending) > 1:
                rest = ", ".join(f"{b['start']} {b['subject']} ({b['type'].lower()}, {b['min']} min)"
                                 for b in pending[1:5])
                lines.append(f"Then: {rest}.")
            return "\n".join(lines)
        lines = []
        for day in days:
            per: dict[str, int] = {}
            for b in day["blocks"]:
                if b["status"] != "skipped":
                    per[b["subject"]] = per.get(b["subject"], 0) + b["min"]
            summary = ", ".join(f"{name} {mins} min" for name, mins in per.items()) or "free"
            lines.append(f"{day['weekday'][:3]} {day['date'][5:]}: {summary}")
        return "\n".join(lines)

    @staticmethod
    def describe_priorities(data: dict[str, Any]) -> str:
        rows = [r for r in data["priorities"] if r.get("weight")]
        if not rows:
            return "There is nothing left to prioritise right now."
        top = rows[0]
        today_min = next(iter(top["minutes_per_day"].values()), 0)
        lines = [f"{top['subject']} comes first: W = {top['weight']:.2f} = urgency {top['urgency']:.2f} "
                 f"({top['days_until_assessment']} day(s) to its assessment) × backlog {top['backlog_pressure']:.2f} "
                 f"({top['backlog_units']} unit(s) behind) × difficulty {top['difficulty_factor']:.2f} × "
                 f"mastery {top['mastery_deficit']:.2f} × exam scope {top['exam_scope_boost']:.2f}. "
                 f"It gets {today_min} min today."]
        if len(rows) > 1:
            lines.append("Then: " + ", ".join(f"{r['subject']} (W {r['weight']:.2f})" for r in rows[1:]) + ".")
        return "\n".join(lines)

    @staticmethod
    def describe_risks(risks: list[dict[str, Any]], reply: Optional[EngineReply] = None) -> str:
        if not risks:
            return "No risks right now: the plan covers every upcoming assessment."
        lines: list[str] = []
        for risk in risks[:2]:
            lines.append(f"⚠ {risk['title']} on {risk['date']}: the plan is {risk['shortfall_min']} min short "
                         f"({risk['needed_min']} needed, {risk['available_min']} planned). Your options:")
            for i, option in enumerate(risk["options"], 1):
                lines.append(f"{i}. {option['detail']}")
                if reply is not None:
                    reply.options.append({"label": option["label"],
                                          "payload": {"type": "risk_option", "title": risk["title"],
                                                      "label": option["label"], "detail": option["detail"],
                                                      "actions": option["actions"]}})
        lines.append("Which one do you want?")
        return "\n".join(lines)

    def _unknown(self, text: str) -> str:
        c = self._context(text)
        if re.search(rf"{_B}behind{_E}", c.text):
            return "How many units are you behind, and in which subject? (for example \"I'm 2 lectures behind in ML\")"
        if find_kind(c.text) and c.mentions:
            return f"When is it? Give me a date like 2026-11-24 or \"in 10 days\" for {c.mentions[0][2]}."
        if re.search(rf"^\s*(hi|hello|hey|salam|مرحبا|السلام عليكم|thanks|thank you|شكرا)\b", c.text):
            return "Hi! Ask me \"what should I study now?\" or tell me what changed today."
        return HELP
