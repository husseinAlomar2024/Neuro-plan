"""Themes, fonts and UI preferences without a window: tokens, contrast, text sizes, set_ui_prefs."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from neuroplan import config
from neuroplan.db.repository import Repository
from neuroplan.events import EventBus
from neuroplan.tools.executor import ToolExecutor
from neuroplan.ui import fonts, theme
from neuroplan.ui.widgets.chat_text import wrap_text

from .conftest import TODAY

PUBLIC_TOKENS = ("bg", "surface", "surface_alt", "border", "text", "text_muted", "accent", "accent_hover",
                 "on_accent", "user_bubble", "coach_bubble", "success", "warning", "danger")
HEX = re.compile(r"^#[0-9A-F]{6}$")


def test_three_themes_midnight_first() -> None:
    assert config.THEME_NAMES == ("Midnight", "Ivory & Gold", "Mist")
    assert set(theme.THEMES) == set(config.THEME_NAMES)
    assert config.DEFAULT_SETTINGS["theme"] == "Midnight"
    assert theme.THEMES["Midnight"].dark and not theme.THEMES["Mist"].dark


@pytest.mark.parametrize("name", config.THEME_NAMES)
def test_every_theme_defines_every_token(name: str) -> None:
    colors = theme.THEMES[name].colors
    assert set(PUBLIC_TOKENS) <= set(theme.TOKENS)
    assert set(colors) == set(theme.TOKENS), "a theme lacks a token or has an unknown one"
    assert all(HEX.match(value) for value in colors.values())


def test_midnight_keeps_the_previous_dark_look() -> None:
    m = theme.THEMES["Midnight"].colors
    assert (m["bg"], m["surface"], m["surface_alt"], m["border"], m["text"], m["text_muted"]) == (
        "#0F1218", "#171B24", "#1F2430", "#2A3040", "#E6E9EF", "#8B93A7")
    assert (m["accent"], m["on_accent"], m["user_bubble"], m["coach_bubble"]) == (
        "#7C9CF5", "#0F1218", "#2B3A66", "#1F2430")


def test_requested_light_palettes() -> None:
    ivory, mist = theme.THEMES["Ivory & Gold"].colors, theme.THEMES["Mist"].colors
    assert (ivory["bg"], ivory["accent"], ivory["on_accent"], ivory["user_bubble"]) == (
        "#FBF8F1", "#C9A24A", "#2B2620", "#F3E3B5")
    assert (mist["bg"], mist["accent"], mist["on_accent"], mist["user_bubble"]) == (
        "#E9EEF3", "#2F7D8C", "#FFFFFF", "#CFE6EA")
    # light coach bubbles have a visible border; the Midnight one does not (unchanged look)
    assert ivory["coach_bubble_border"] != ivory["coach_bubble"]
    assert mist["coach_bubble_border"] != mist["coach_bubble"]


@pytest.mark.parametrize("name", config.THEME_NAMES)
def test_body_text_contrast(name: str) -> None:
    c = theme.THEMES[name].colors
    for fg in ("text", "text_muted"):
        for bg in ("bg", "surface", "surface_alt", "coach_bubble"):
            ratio = theme.contrast_ratio(c[fg], c[bg])
            assert ratio >= 4.5, f"{name}: {fg} on {bg} is {ratio:.2f}:1"
    assert theme.contrast_ratio(c["text"], c["user_bubble"]) >= 4.5
    assert theme.contrast_ratio(c["text"], c["surface_hover"]) >= 4.5     # confirm cards
    for bg in ("accent", "accent_hover"):                                  # primary buttons, also hovered
        ratio = theme.contrast_ratio(c["on_accent"], c[bg])
        assert ratio >= 4.5, f"{name}: on_accent on {bg} is {ratio:.2f}:1"
    for bg in ("surface", "coach_bubble", "surface_alt"):                 # Undo, options, "today" label
        assert theme.contrast_ratio(c["accent_text"], c[bg]) >= 4.5


@pytest.mark.parametrize("name", config.THEME_NAMES)
def test_status_colours_stay_readable(name: str) -> None:
    """Backlog bars, status words and the "Actions executed" lines: at least 3:1 (WCAG non-text and
    bold text) on every surface they appear on; the progress-bar track is distinguishable too."""
    c = theme.THEMES[name].colors
    for fg in ("success", "warning", "danger"):
        for bg in ("bg", "surface", "surface_alt", "coach_bubble"):
            ratio = theme.contrast_ratio(c[fg], c[bg])
            assert ratio >= 3.0, f"{name}: {fg} on {bg} is {ratio:.2f}:1"
    assert theme.contrast_ratio(c["danger"], c["banner_bg"]) >= 3.0
    assert theme.contrast_ratio(c["on_danger"], c["danger"]) >= 3.0       # unread badge
    assert theme.contrast_ratio(c["text_muted"], c["user_bubble"]) >= 3.0


def test_contrast_ratio_reference_values() -> None:
    assert theme.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_tokens_resolve_to_the_current_theme() -> None:
    before = theme.STATE.name
    try:
        theme.STATE.name = "Mist"
        assert theme.BG[0] == theme.BG[1] == "#E9EEF3" and theme.resolve(theme.BG) == "#E9EEF3"
        assert list(theme.TEXT) == ["#1E2A36", "#1E2A36"]
        theme.STATE.name = "Ivory & Gold"
        assert theme.BG[1] == "#FBF8F1"
        assert theme.resolve("#123456") == "#123456"
    finally:
        theme.STATE.name = before
    with pytest.raises(KeyError):
        theme.Token("nope")


def test_theme_names_cycle_and_legacy_values() -> None:
    assert theme.next_theme("Midnight") == "Ivory & Gold"
    assert theme.next_theme("Ivory & Gold") == "Mist"
    assert theme.next_theme("Mist") == "Midnight"
    assert config.normalize_theme("Dark") == "Midnight"
    assert config.normalize_theme("Light") == "Ivory & Gold"
    assert config.normalize_theme("garbage") == "Midnight"


def test_no_colour_literals_outside_theme_module() -> None:
    ui = Path(__file__).resolve().parent.parent / "neuroplan" / "ui"
    offenders = [f"{path.name}:{n}" for path in ui.rglob("*.py") if path.name != "theme.py"
                 for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if re.search(r"#[0-9A-Fa-f]{6}\b|\"(white|black|gray\d*)\"", line)]
    assert not offenders, offenders


def test_text_sizes_scale_every_font() -> None:
    book = fonts.FontBook()
    assert config.CHAT_TEXT_SIZES == {"Small": 13, "Medium": 15, "Large": 17}
    assert (book.scaled(config.CHAT_FONT_SIZE), book.scaled(config.UI_FONT_SIZE)) == (15, 13)
    book.text_size = "Small"
    assert (book.scaled(15), book.scaled(13)) == (13, 11)
    book.text_size = "Large"
    assert (book.scaled(15), book.scaled(13)) == (17, 15)
    assert fonts.step_text_size("Medium", 1) == "Large" and fonts.step_text_size("Large", 1) == "Large"
    assert fonts.step_text_size("Medium", -1) == "Small" and fonts.step_text_size("Small", -1) == "Small"
    assert fonts.step_text_size("Large", 0) == "Medium"
    assert fonts.FAMILY == "Segoe UI"


def test_wrap_text_breaks_words_and_long_words() -> None:
    measure = len                                   # one px per character
    assert wrap_text("aaa bbb ccc", 7, measure, 1) == ["aaa bbb", "ccc"]
    assert wrap_text("one\n\ntwo", 10, measure, 1) == ["one", "", "two"]
    lines = wrap_text("x" * 25, 10, measure, 1)
    assert lines == ["x" * 10, "x" * 10, "x" * 5]
    assert all(len(line) <= 10 for line in wrap_text("word " * 30 + "y" * 33, 10, measure, 1))


# ---------------------------------------------------------------- set_ui_prefs / get_settings
@pytest.fixture
def executor(seeded: Repository) -> ToolExecutor:
    return ToolExecutor(seeded, EventBus(), lambda: TODAY)


def test_ui_prefs_are_stored_without_undo_or_events(executor: ToolExecutor, seeded: Repository) -> None:
    seen: list[str] = []
    executor.bus.subscribe("state_changed", lambda e: seen.append(e.name))
    actions = len(seeded.list_actions(100))
    result = executor.execute("set_ui_prefs", {"prefs": {"pane_coach_width": 455, "theme": "Mist",
                                                         "text_size": "Large", "pane_subjects_list": "333"}})
    assert result.ok, result.summary
    assert seeded.get_setting("pane_coach_width") == "455" and seeded.get_setting("pane_subjects_list") == "333"
    assert seeded.get_setting("theme") == "Mist" and seeded.get_setting("text_size") == "Large"
    assert len(seeded.list_actions(100)) == actions and not seen, "preferences are not undoable actions"
    settings = executor.execute("get_settings").data
    assert settings["pane_coach_width"] == "455" and settings["theme"] == "Mist"


@pytest.mark.parametrize("prefs", [{"theme": "Neon"}, {"text_size": "Huge"}, {"pane_coach_width": "wide"},
                                   {"pane_coach_width": 0}, {"window": 3}])
def test_ui_prefs_are_validated(executor: ToolExecutor, seeded: Repository, prefs: dict[str, object]) -> None:
    result = executor.execute("set_ui_prefs", {"prefs": prefs})
    assert not result.ok
    assert seeded.get_setting("pane_coach_width") is None


def test_legacy_theme_values_are_mapped(executor: ToolExecutor, seeded: Repository) -> None:
    seeded.set_setting("theme", "Light")
    assert executor.execute("get_settings").data["theme"] == "Ivory & Gold"
    assert executor.execute("update_settings", {"settings": {"theme": "Dark"}}).ok
    assert seeded.get_setting("theme") == "Midnight"
    assert executor.execute("update_settings", {"settings": {"text_size": "Small"}}).ok
    assert not executor.execute("update_settings", {"settings": {"theme": "Neon"}}).ok
    defaults = executor.execute("get_settings").data
    assert defaults["pane_subjects_list"] == str(config.SUBJECTS_LIST_DEFAULT_WIDTH)
