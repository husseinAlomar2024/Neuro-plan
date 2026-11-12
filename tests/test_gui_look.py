"""GUI: draggable dividers that persist, live text size, live themes, DPI awareness.
Skipped automatically when no display is available."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from neuroplan import config
from neuroplan.db.repository import Repository
from neuroplan.models import ChatMessage

from .conftest import TODAY
from .test_gui_smoke import _display_available, make_app, pump

pytestmark = pytest.mark.skipif(not _display_available(), reason="no display available")

_COLOR_ATTRS = ("fg_color", "bg_color", "border_color", "text_color", "hover_color", "button_color",
                "button_hover_color", "progress_color", "text_color_disabled", "placeholder_text_color",
                "scrollbar_button_color", "scrollbar_button_hover_color", "dropdown_fg_color",
                "dropdown_hover_color", "dropdown_text_color")


def _new_app(repo: Repository) -> Any:
    app = make_app(repo)
    app.geometry("1500x800")                 # room for the coach panel to grow (centre minimum 710)
    pump(app, 0.3)
    return app


@pytest.fixture
def app(seeded: Repository) -> Iterator[Any]:
    instance = _new_app(seeded)
    yield instance
    try:
        instance.destroy()
    except Exception:
        pass


def _walk(widget: Any) -> Iterator[Any]:
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _drag(divider: Any, dx_unscaled: int) -> None:
    import customtkinter as ctk
    scale = ctk.ScalingTracker.get_widget_scaling(divider)
    divider.press(1000)
    divider.drag_to(1000 + round(dx_unscaled * scale / 2))      # an intermediate motion event
    divider.drag_to(1000 + round(dx_unscaled * scale))
    divider.release()


# ---------------------------------------------------------------- dividers
def test_coach_divider_drags_clamps_persists_and_resets(seeded: Repository) -> None:
    app = _new_app(seeded)
    try:
        app.deiconify()
        pump(app, 0.3)
        divider = app.coach_divider
        assert int(divider.cget("width")) == config.DIVIDER_WIDTH and divider.cget("cursor") == "sb_h_double_arrow"
        divider._enter()
        assert divider.cget("fg_color") != "transparent", "the strip highlights on hover"
        divider._leave()
        assert divider.cget("fg_color") == "transparent"
        assert app.panel.width == config.COACH_DEFAULT_WIDTH
        area = app.panel._area
        _drag(divider, -100)                                   # left = wider coach panel
        pump(app, 0.3)
        assert app.panel.width == config.COACH_DEFAULT_WIDTH + 100
        assert app.panel._area > area + 60, "chat bubbles re-wrap while dragging"
        assert seeded.get_setting("pane_coach_width") == str(config.COACH_DEFAULT_WIDTH + 100)
        _drag(divider, 2000)                                   # far right: clamped at the minimum
        assert app.panel.width == config.COACH_MIN_WIDTH
        _drag(divider, -3000)                                  # far left: the centre keeps its minimum
        assert app.panel.width == app._coach_max_width() < 3000
        _drag(divider, app.panel.width - 444)
        assert seeded.get_setting("pane_coach_width") == "444"
    finally:
        app.destroy()
    app = _new_app(seeded)                                     # restored on the next launch
    try:
        assert app.panel.width == 444
        app.coach_divider._double(None)                        # double click: back to the default
        assert app.panel.width == config.COACH_DEFAULT_WIDTH
        assert seeded.get_setting("pane_coach_width") == str(config.COACH_DEFAULT_WIDTH)
    finally:
        app.destroy()


def test_subjects_divider_persists_and_keeps_both_columns(seeded: Repository) -> None:
    app = _new_app(seeded)
    try:
        app.deiconify()
        app.show_view("Subjects")
        pump(app, 0.4)
        view = app.views["Subjects"]
        assert view.list_width == config.SUBJECTS_LIST_DEFAULT_WIDTH
        _drag(view.divider, -80)
        pump(app, 0.3)
        assert view.list_width == config.SUBJECTS_LIST_DEFAULT_WIDTH - 80
        assert seeded.get_setting("pane_subjects_list") == str(config.SUBJECTS_LIST_DEFAULT_WIDTH - 80)
        _drag(view.divider, -1000)
        assert view.list_width == config.COLUMN_MIN_WIDTH
        _drag(view.divider, 3000)                              # the form column keeps its minimum too
        room = view._columns_width()
        assert view.list_width == room - config.DIVIDER_WIDTH - 2 - config.COLUMN_MIN_WIDTH
        _drag(view.divider, 300 - view.list_width)
        pump(app, 0.3)
        # subject cards re-wrap to the narrower list: no text is wider than the list
        canvas = view.table._parent_canvas
        for row in view.table.winfo_children():
            for widget in _walk(row):
                assert widget.winfo_reqwidth() <= canvas.winfo_width() + 2
    finally:
        app.destroy()
    app = _new_app(seeded)
    try:
        view = app.views["Subjects"]
        assert view.list_width == 300
        view.divider._double(None)
        assert view.list_width == config.SUBJECTS_LIST_DEFAULT_WIDTH
        assert seeded.get_setting("pane_subjects_list") == str(config.SUBJECTS_LIST_DEFAULT_WIDTH)
    finally:
        app.destroy()


def test_collapsed_coach_hides_its_divider(app: Any) -> None:
    app.deiconify()
    pump(app, 0.2)
    app.panel.toggle()
    pump(app, 0.1)
    assert not app.coach_divider.winfo_ismapped()
    app.panel.toggle()
    pump(app, 0.1)
    assert app.coach_divider.winfo_ismapped()
    assert int(app.panel.cget("width")) == app.panel.width


# ---------------------------------------------------------------- text size
def test_text_size_setting_applies_live_and_persists(app: Any, seeded: Repository) -> None:
    from neuroplan.ui import fonts, theme
    from neuroplan.ui.widgets.chat_text import ChatText
    app.deiconify()
    app.panel.add_message(ChatMessage(9100, "", "coach", "A coach message long enough to wrap onto a few "
                                                         "lines inside the bubble of the panel."))
    pump(app, 0.3)
    bubble = next(w for w, _inset in reversed(app.panel._wrapped) if isinstance(w, ChatText))
    assert fonts.chat().cget("size") == 15 and theme.font(13).cget("size") == 13
    assert fonts.chat().cget("family") == "Segoe UI"
    height = bubble.winfo_reqheight()
    lines = len(bubble.lines)
    assert height > lines * bubble._tkfont.metrics("linespace"), "bubbles have extra line spacing"

    app.step_text_size(1)                                     # Ctrl+Plus
    pump(app, 0.2)
    assert fonts.text_size() == "Large" and seeded.get_setting("text_size") == "Large"
    assert fonts.chat().cget("size") == 17 and theme.font(13).cget("size") == 15
    assert bubble.winfo_reqheight() > height, "the bubble re-measured itself with the bigger font"
    app.step_text_size(1)
    assert fonts.text_size() == "Large"
    app.step_text_size(-1)
    app.step_text_size(-1)                                    # Ctrl+Minus twice
    assert fonts.text_size() == "Small" and fonts.chat().cget("size") == 13
    app.step_text_size(0)                                     # Ctrl+0
    assert fonts.text_size() == "Medium" and seeded.get_setting("text_size") == "Medium"
    app.event_generate("<Control-plus>")                     # the real key binding
    pump(app, 0.1)
    assert fonts.text_size() == "Large"
    app.set_text_size("Small")
    app.destroy()
    again = _new_app(seeded)
    try:
        assert fonts.text_size() == "Small" and fonts.chat().cget("size") == 13
        settings = again.views["Settings"]
        again.show_view("Settings")
        pump(again, 0.1)
        assert settings.v["text_size"].get() == "Small"
        settings.app_text_size("Medium")                      # the Settings dropdown applies at once
        assert fonts.chat().cget("size") == 15
    finally:
        again.destroy()


# ---------------------------------------------------------------- themes
def _assert_themed(app: Any, name: str, subject_colors: set[str]) -> None:
    from neuroplan.ui import theme
    from neuroplan.ui.widgets.chat_text import ChatText
    colors = theme.THEMES[name].colors
    palette = {v.upper() for v in colors.values()}
    assert app.cget("bg").upper() == colors["bg"]
    assert app.panel.messages._parent_canvas.cget("bg").upper() == colors["bg"]
    checked = 0
    for widget in _walk(app):
        if isinstance(widget, ChatText):
            assert widget.cget("bg").upper() in (colors["user_bubble"], colors["coach_bubble"])
            assert all(widget.itemcget(i, "fill").upper() == colors["text"] for i in widget.find_withtag("line"))
            continue
        if not hasattr(widget, "_apply_appearance_mode"):
            continue
        for attr in _COLOR_ATTRS:
            try:
                value = widget.cget(attr)
            except Exception:
                continue
            if value is None or value == "transparent":
                continue
            if isinstance(value, theme.Token):
                assert widget._apply_appearance_mode(value).upper() in palette
                checked += 1
                continue
            assert isinstance(value, str) and value.upper() in subject_colors, \
                f"{type(widget).__name__}.{attr} = {value!r} does not follow the theme"
    assert checked > 500


def test_themes_switch_live_and_everything_follows(app: Any, seeded: Repository) -> None:
    from neuroplan.ui import theme
    subject_colors = {s.color_hex.upper() for s in seeded.list_subjects()}
    app.deiconify()
    app.panel.add_message(ChatMessage(9200, "", "user", "hello"))
    app.panel.add_message(ChatMessage(9201, "", "coach", "Done.", {
        "actions": [{"tool": "set_day_override", "ok": True, "summary": "Set today", "action_id": 1}],
        "confirmations": [{"tool": "delete_subject", "args": {}, "preview": "Delete X?"}]}))
    for name in ("Today", "Week", "Subjects", "Reviews", "Settings"):
        app.show_view(name)
        pump(app, 0.1)
    assert theme.current_theme() == "Midnight"
    _assert_themed(app, "Midnight", subject_colors)
    app.cycle_theme()                                          # the sidebar button
    pump(app, 0.2)
    assert theme.current_theme() == "Ivory & Gold" and seeded.get_setting("theme") == "Ivory & Gold"
    assert "Ivory & Gold" in app.theme_button.cget("text")
    _assert_themed(app, "Ivory & Gold", subject_colors)
    app.views["Settings"].app_theme("Mist")                    # the Settings selector
    pump(app, 0.2)
    _assert_themed(app, "Mist", subject_colors)
    for name in ("Today", "Week", "Subjects", "Reviews", "Settings"):     # views rebuilt after the switch
        app.views[name]._signature = ""
        app.show_view(name)
        pump(app, 0.1)
    _assert_themed(app, "Mist", subject_colors)
    app.cycle_theme()
    pump(app, 0.2)
    assert theme.current_theme() == "Midnight"
    _assert_themed(app, "Midnight", subject_colors)


def test_theme_is_restored_on_launch(seeded: Repository) -> None:
    from neuroplan.ui import theme
    seeded.set_setting("theme", "Light")                      # a value stored by an older version
    app = _new_app(seeded)
    try:
        assert theme.current_theme() == "Ivory & Gold"
        assert app.cget("bg").upper() == theme.THEMES["Ivory & Gold"].colors["bg"]
    finally:
        app.destroy()


# ---------------------------------------------------------------- DPI
@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows only")
def test_process_is_per_monitor_dpi_aware_before_tk_starts(tmp_path: Path) -> None:
    """Run in a fresh process: importing the UI makes it per-monitor aware before the first Tk window,
    so Tk measures the real DPI (tk scaling = DPI / 72) and CustomTkinter scales only once."""
    script = (
        "import ctypes, customtkinter as ctk\n"
        "from neuroplan.ui import dpi, theme\n"
        "assert dpi.process_awareness() == dpi.PER_MONITOR, dpi.process_awareness()\n"
        "app = ctk.CTk(); app.update()\n"
        "hwnd = int(app.wm_frame(), 16)\n"
        "real = ctypes.windll.user32.GetDpiForWindow(hwnd)\n"
        "tk_dpi = float(app.tk.call('tk', 'scaling')) * 72\n"
        "assert abs(tk_dpi - real) < 2, (tk_dpi, real)\n"
        "scale = ctk.ScalingTracker.get_widget_scaling(app)\n"
        "assert abs(scale - real / 96) < 0.01, (scale, real)\n"
        "label = theme.label(app, 'x', 15); label.pack(); app.update()\n"
        "import re\n"
        "size = int(re.search(r' (-?[0-9]+) ', str(label._label.cget('font'))).group(1))\n"
        "assert size == -round(15 * scale), (size, scale)\n"
        "app.destroy(); print('ok')\n")
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr


# ---------------------------------------------------------------- smooth dragging
def test_drag_moves_only_a_ghost_line_and_resizes_once_on_release(app: Any, seeded: Repository) -> None:
    """50 motion events in one drag: no pane width or wraplength changes until the button is released,
    then the width is applied exactly once and the texts re-wrap exactly once."""
    from types import SimpleNamespace

    import customtkinter as ctk

    from neuroplan.ui.widgets.chat_text import ChatText
    app.deiconify()
    panel, divider = app.panel, app.coach_divider
    panel.add_message(ChatMessage(9300, "", "coach", "A long coach answer that wraps over several lines in the "
                                                     "bubble, so a wider panel gives it fewer lines. " * 3))
    pump(app, 0.4)
    bubble = next(w for w, _inset in reversed(panel._wrapped) if isinstance(w, ChatText))
    applied: list[int] = []
    real_apply = divider.on_apply
    divider.on_apply = lambda w: (applied.append(w), real_apply(w))
    rewraps: list[int] = []
    real_rewrap = panel.rewrap

    def spy_rewrap(width: int) -> None:
        if width != panel._area:
            rewraps.append(width)
        real_rewrap(width)
    panel.rewrap = spy_rewrap
    wraps: list[int] = []
    real_configure = bubble.configure

    def spy_configure(cnf: Any = None, **kwargs: Any) -> Any:
        if "wraplength" in kwargs:
            wraps.append(kwargs["wraplength"])
        return real_configure(cnf, **kwargs)
    bubble.configure = spy_configure

    scale = ctk.ScalingTracker.get_widget_scaling(divider)
    width0, shown0, wrap0, area0 = panel.width, int(panel.cget("width")), bubble.cget("wraplength"), panel._area
    canvas_width0 = panel.messages._parent_canvas.winfo_width()
    divider._press(SimpleNamespace(x_root=1000))
    xs: list[int] = []
    for i in range(1, 51):                                   # 50 motion events, 2 px each, to the left
        divider._motion(SimpleNamespace(x_root=1000 - round(i * 2 * scale)))
        app.update()
        xs.append(divider.ghost_x())
        assert divider.ghost_x() is not None, "the ghost line follows the mouse"
        assert panel.width == width0 and int(panel.cget("width")) == shown0, "no resize while dragging"
        assert bubble.cget("wraplength") == wrap0 and panel._area == area0
    assert xs == sorted(xs, reverse=True) and xs[0] > xs[-1] >= 0
    assert panel.messages._parent_canvas.winfo_width() == canvas_width0
    assert not applied and not rewraps and not wraps
    divider._release(SimpleNamespace())
    # applied synchronously, in one pass: width, re-wrap and scroll are final when the release returns
    assert divider.ghost_x() is None
    assert applied == [width0 + 100], "the width is applied once, on release"
    assert panel.width == width0 + 100 and seeded.get_setting("pane_coach_width") == str(width0 + 100)
    assert len(rewraps) == 1 and len(wraps) == 1, "the texts re-wrap exactly once, in the same pass"
    assert bubble.cget("wraplength") > wrap0
    pump(app, 0.5)
    assert len(rewraps) == 1 and len(wraps) == 1, "no delayed second pass"

    # the line stops at the limit instead of following the mouse past it
    divider.press(1000)
    divider.drag_to(1000 + 5000)
    limit_x = divider.ghost_x()
    divider.drag_to(1000 + 9000)
    assert divider.ghost_x() == limit_x and divider.pending_width == config.COACH_MIN_WIDTH
    divider.release()
    pump(app, 0.3)
    assert panel.width == config.COACH_MIN_WIDTH and len(applied) == 2


def test_window_resize_fits_panes_once_after_the_burst(app: Any) -> None:
    """Window resize / maximise / restore: the panes are fitted and the texts re-wrapped once after the
    burst of <Configure> events; hidden views are not gridded, so they are not resized at all."""
    from neuroplan.ui.widgets.chat_text import ChatText
    app.deiconify()
    pump(app, 0.4)
    app._fit_panes.ms = 3000                                 # a whole burst falls inside one debounce window
    app.panel._rewrap_later.ms = 3000
    runs, rewraps = app._fit_panes.runs, app.panel._rewrap_later.runs
    texts = [w for w, _i in app.panel._wrapped if isinstance(w, ChatText)]
    for width in range(1100, 1400, 30):                      # e.g. a window edge dragged, or maximise/restore
        app.geometry(f"{width}x800")
        app.update()
    assert app._fit_panes.runs == runs and app.panel._rewrap_later.runs == rewraps, "nothing per event"
    pump(app, until=lambda: not app._fit_panes.pending and not app.panel._rewrap_later.pending)
    assert app._fit_panes.runs == runs + 1 and app.panel._rewrap_later.runs <= rewraps + 1
    assert all(t.winfo_exists() for t in texts), "resizing reconfigures widgets, it never rebuilds them"
    assert [n for n, v in app.views.items() if v.winfo_ismapped()] == [app.current]


def test_escape_and_focus_loss_cancel_a_drag(app: Any) -> None:
    app.deiconify()
    pump(app, 0.3)
    divider, width = app.coach_divider, app.panel.width
    divider.press(1000)
    divider.drag_to(900)
    assert divider.ghost_x() is not None
    app.cancel_drags()                                      # what <Escape> and <Deactivate> call
    assert divider.ghost_x() is None and not divider.dragging
    divider.release()                                       # the button-up after a cancel does nothing
    assert app.panel.width == width
    divider.press(1000)
    divider.drag_to(950)
    app.event_generate("<Escape>")
    pump(app, 0.1)
    assert not divider.dragging and app.panel.width == width


# ---------------------------------------------------------------- chat scroll after width changes (D)
def _visible_holders(panel: Any) -> list[int]:
    canvas = panel.messages._parent_canvas
    top, height = canvas.winfo_rooty(), canvas.winfo_height()
    return [i for i, w in enumerate(panel.messages.winfo_children())
            if w.winfo_rooty() - top < height and w.winfo_rooty() - top + w.winfo_height() > 0]


def _add_chat(panel: Any) -> None:
    long = "A long coach answer that wraps over several lines and gets much shorter in a wide panel. " * 3
    for i in range(12):
        panel.add_message(ChatMessage(9400 + i, "", "coach" if i % 2 else "user", long if i % 3 == 0 else "ok"))


def _drag_coach_to(app: Any, width: int) -> None:
    divider = app.coach_divider
    divider.press(1000)
    divider.drag_to(1000 - round((width - app.panel.width) * app._scale()))
    divider.release()


def test_chat_keeps_its_bottom_and_is_never_blank_after_min_max_min(app: Any) -> None:
    app.deiconify()
    panel = app.panel
    _add_chat(panel)
    pump(app, 0.4)
    last = len(panel.messages.winfo_children()) - 1
    for width in (config.COACH_MIN_WIDTH, 4000, config.COACH_MIN_WIDTH, 4000):
        _drag_coach_to(app, width)
        visible = _visible_holders(panel)
        assert visible, f"the message list is blank at width {panel.width}"
        assert visible[-1] == last and panel.messages._parent_canvas.yview()[1] >= 0.999, "bottom stays bottom"
    assert panel.width == app._coach_max_width()


def test_chat_keeps_the_message_at_the_top_edge(app: Any) -> None:
    app.deiconify()
    panel = app.panel
    _add_chat(panel)
    pump(app, 0.4)
    _drag_coach_to(app, config.COACH_MIN_WIDTH)
    canvas = panel.messages._parent_canvas
    children = panel.messages.winfo_children()
    canvas.yview_moveto(children[2].winfo_y() / canvas.bbox("all")[3])     # message 2 at the top edge
    app.update()
    assert _visible_holders(panel)[0] == 2
    for width in (4000, config.COACH_MIN_WIDTH + 40):
        _drag_coach_to(app, width)
        visible = _visible_holders(panel)
        assert visible and visible[0] == 2, (width, visible)
        assert abs(children[2].winfo_rooty() - canvas.winfo_rooty()) <= 2
    # near the end, a wider panel makes the content shorter: the view is clamped to the end, not blank
    canvas.yview_moveto(children[9].winfo_y() / canvas.bbox("all")[3])
    app.update()
    _drag_coach_to(app, 4000)
    visible = _visible_holders(panel)
    assert visible and visible[-1] == len(children) - 1 and canvas.yview()[1] >= 0.999
    # content shorter than the view: clamped to the top instead of showing empty space
    for child in children[1:]:
        child.destroy()
    app.apply_layout(lambda: app.panel.set_width(config.COACH_MIN_WIDTH))
    assert _visible_holders(panel) == [0] and canvas.yview()[0] == 0.0


# ---------------------------------------------------------------- limits (F)
def test_pane_limits_keep_the_centre_view_unclipped(app: Any) -> None:
    from neuroplan.ui import fonts, theme
    from tools.centre_min import clipped
    app.deiconify()
    pump(app, 0.3)
    assert config.CENTRE_MIN_WIDTHS == {"Small": 670, "Medium": 710, "Large": 740}
    window = round(app.winfo_width() / app._scale())
    assert app.centre_min_width() == 710
    assert app._coach_max_width() == window - theme.SIDEBAR_WIDTH - config.DIVIDER_WIDTH - 710
    assert app.window_min_width() == theme.SIDEBAR_WIDTH + config.DIVIDER_WIDTH + config.COACH_MIN_WIDTH + 710
    assert abs(app.wm_minsize()[0] - app.window_min_width() * app._scale()) <= 1
    # the ghost line stops at the centre's minimum, and that is where the pane ends up
    divider = app.coach_divider
    divider.press(1000)
    assert divider.drag_to(1000 - 4000) == app._coach_max_width()
    limit = divider.ghost_x()
    divider.drag_to(1000 - 6000)
    assert divider.ghost_x() == limit
    divider.release()
    assert app.panel.width == app._coach_max_width()
    assert abs(round(app.centre.winfo_width() / app._scale()) - 710) <= 1
    assert clipped(app.views["Today"]) == [], "every Today card is whole at the minimum centre width"
    # a smaller window: the coach panel gives way first, the centre keeps its minimum
    app.geometry(f"{app.window_min_width() + 40}x800")
    pump(app, until=lambda: not app._fit_panes.pending)
    pump(app, 0.2)
    assert int(app.panel.cget("width")) == app._coach_max_width() < app.panel.width   # preference kept
    assert round(app.centre.winfo_width() / app._scale()) >= 709
    # a larger text size needs a wider centre view
    app.set_text_size("Large", store=False)
    assert app.centre_min_width() == 740 and fonts.text_size() == "Large"
    assert abs(app.wm_minsize()[0] - app.window_min_width() * app._scale()) <= 1


def test_chat_renders_the_latest_40_and_loads_older_at_the_top(app: Any, seeded: Repository) -> None:
    for i in range(70):
        seeded.add_message("coach" if i % 2 else "user", f"message {i}")
    panel = app.panel
    panel.load_history()
    app.deiconify()
    pump(app, 0.4)
    texts = lambda: [w.text for w, _i in panel._wrapped if hasattr(w, "text")]  # noqa: E731
    assert len(panel.messages.winfo_children()) == config.CHAT_RENDERED and panel.has_older
    assert texts()[-1] == "message 69" and "message 29" not in texts()
    canvas = panel.messages._parent_canvas
    first = panel.messages.winfo_children()[0]
    canvas.yview_moveto(0.0)
    pump(app, 0.3)                                   # the poll loads 20 older ones, the top message stays put
    assert len(panel.messages.winfo_children()) >= config.CHAT_RENDERED + config.CHAT_LOAD_MORE
    assert "message 29" in texts()
    assert abs(first.winfo_rooty() - canvas.winfo_rooty()) <= 2, "no jump: the same message is at the top edge"
    by_y = sorted((w.winfo_rooty(), int(w.text.split()[1])) for w, _i in panel._wrapped
                  if hasattr(w, "text") and w.text.startswith("message "))
    order = [n for _y, n in by_y]
    assert order == sorted(order), "older messages are shown above the newer ones"
