"""Subject name matching shared by the executor and the offline parser."""
from __future__ import annotations

import difflib
import re
from typing import Optional

from .. import config


def normalize(text: str) -> str:
    """Lower-case, ASCII digits, collapsed whitespace."""
    return re.sub(r"\s+", " ", text.translate(config.ARABIC_DIGITS).lower()).strip()


def acronym(name: str) -> str:
    words = re.findall(r"[^\W\d_]+", name)
    return "".join(w[0] for w in words).lower() if len(words) >= 2 else ""


def subject_keys(name: str) -> list[str]:
    """Every spelling that identifies a subject: full name, acronym, configured aliases."""
    canonical = normalize(name)
    keys = [canonical]
    short = acronym(name)
    if len(short) >= 2:
        keys.append(short)
    keys.extend(alias for alias, target in config.SUBJECT_ALIASES.items() if target == canonical)
    return keys


def match_subject(query: str, names: list[str]) -> Optional[str]:
    """Best subject name for a free-text reference, or None."""
    q = normalize(query).strip(" .,!?:;\"'")
    if not q:
        return None
    lookup: dict[str, str] = {}
    for name in names:
        for key in subject_keys(name):
            lookup.setdefault(key, name)
    if q in lookup:
        return lookup[q]
    for key, name in lookup.items():       # "deep learning course" / "the ml"
        if len(key) > 3 and (key in q or (len(q) > 3 and q in key)):
            return name
    close = difflib.get_close_matches(q, list(lookup), n=1, cutoff=0.78)
    return lookup[close[0]] if close else None


def find_subjects(text: str, names: list[str]) -> list[tuple[int, int, str]]:
    """Non-overlapping (start, end, name) mentions of known subjects in ``text``, in text order."""
    lowered = normalize_keep_length(text)
    candidates: list[tuple[int, int, str]] = []
    for name in names:
        for key in subject_keys(name):
            pattern = r"(?<![^\W_])" + re.escape(key) + r"(?![^\W_])"
            for m in re.finditer(pattern, lowered):
                candidates.append((m.start(), m.end(), name))
    candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
    chosen: list[tuple[int, int, str]] = []
    for start, end, name in candidates:
        if all(end <= s or start >= e for s, e, _ in chosen):
            chosen.append((start, end, name))
    return sorted(chosen)


def normalize_keep_length(text: str) -> str:
    """Lower-case + ASCII digits without changing string length (so offsets stay valid)."""
    return text.translate(config.ARABIC_DIGITS).lower()
