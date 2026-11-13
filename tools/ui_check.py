"""Dev tool: measure and capture the Qt UI on screen.

Launches the Qt app on a temporary seeded database with 200 chat messages (English and Arabic, long and
short, some with option buttons) and:

  (a) drags every splitter handle through 60 steps,
  (b) resizes the window through 60 sizes,
  (c) scrolls the chat from the top to the bottom,

timing every pass: the change (``QSplitter.moveSplitter`` — what the handle calls on each mouse move —,
``resize``, scroll-bar value), then all posted layout events and a synchronous repaint of the whole
window. Reports median, 95th percentile and worst pass per scenario.

  (d) saves ``widget.grab()`` PNGs of every view in every theme at 1280x800 and 1024x700 into a
      temporary folder (deleted at the end unless --keep).

    .venv\\Scripts\\python tools\\ui_check.py [--keep] [--no-shots] [--json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from neuroplan import config  # noqa: E402
from neuroplan.db.repository import Repository  # noqa: E402
from neuroplan.seed import seed_demo  # noqa: E402

STEPS = 60
SIZES = ((1280, 800), (1024, 700))
ENGLISH_LONG = ("Here is the plan for this week. Machine Learning needs two catch-up blocks because you are "
                "three units behind, and the midterm is in nine days, so those come first in your peak window. ")
ARABIC_LONG = ("لدي امتحان في الإحصاء يوم الأحد القادم، وأحتاج إلى خطة مراجعة واضحة لهذا الأسبوع مع وقت "
               "كافٍ للتمارين والمراجعة النهائية قبل الامتحان. ")
ARABIC_SHORT = "شكراً، هذا مفيد جداً"


def fill_chat(repo: Repository, count: int = 200) -> None:
    for i in range(count):
        role = "coach" if i % 2 else "user"
        if i % 10 == 3:
            text = ARABIC_LONG * (1 + i % 2)
        elif i % 10 == 6:
            text = ARABIC_SHORT
        elif i % 4 == 1:
            text = ENGLISH_LONG * (1 + i % 3)
        else:
            text = f"Short message {i}"
        actions = None
        if role == "coach" and i % 9 == 1:
            actions = {"options": [{"label": label, "payload": {"type": "noop"}}
                                   for label in ("No time", "Too hard", "Sick")]}
        repo.add_message(role, text, actions)


def pass_time(app: QApplication, window: Any, change: Callable[[], None]) -> float:
    """One layout+paint pass: the change, posted events (layout requests, timers due now), a full repaint."""
    start = time.perf_counter()
    change()
    app.sendPostedEvents()
    app.processEvents()
    window.repaint()
    return (time.perf_counter() - start) * 1000


def stats(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {"passes": len(samples), "median_ms": round(statistics.median(ordered), 1),
            "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 1), "worst_ms": round(ordered[-1], 1),
            "over_16ms": sum(1 for s in ordered if s > 16.0)}


def drag_splitter(app: QApplication, window: Any, splitter: Any, low: int, high: int) -> list[float]:
    """Move handle 1 from ``low`` to ``high`` and back in STEPS passes."""
    times = []
    positions = [round(low + (high - low) * i / (STEPS // 2)) for i in range(STEPS // 2)]
    for pos in positions + positions[::-1]:
        times.append(pass_time(app, window, lambda p=pos: splitter.moveSplitter(p, 1)))
    return times


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="keep the PNG folder")
    parser.add_argument("--no-shots", action="store_true", help="skip the screenshots")
    parser.add_argument("--json", action="store_true", help="print results as JSON lines")
    args = parser.parse_args()

    from neuroplan.ui_qt.app import MainWindow, ensure_app
    app = ensure_app()
    tmp = Path(tempfile.mkdtemp(prefix="np_uicheck_"))
    repo = Repository(tmp / "check.db", fast=True)
    seed_demo(repo, date.today())
    fill_chat(repo)
    window = MainWindow(repo, run_startup=False)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.resize(1280, 800)
    window.move(40, 30)
    window.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.01)
    results: dict[str, Any] = {}

    # (a) splitters
    main_split = window.splitter
    total = sum(main_split.sizes())
    low = total - window.panel.maximumWidth() if window.panel.maximumWidth() < total else 0
    results["drag coach splitter"] = stats(drag_splitter(
        app, window, main_split, max(window.centre.minimumSizeHint().width(), low) + 5,
        total - config.COACH_MIN_WIDTH - 5))
    window.show_view("Subjects")
    app.processEvents()
    sub = window.views["Subjects"].splitter
    sub_total = sum(sub.sizes())
    results["drag subjects splitter"] = stats(drag_splitter(
        app, window, sub, config.COLUMN_MIN_WIDTH + 5, sub_total - config.COLUMN_MIN_WIDTH - 5))
    window.show_view("Today")
    app.processEvents()

    # (b) window sizes
    times = []
    sizes = [(1024 + round(500 * i / (STEPS // 2)), 700 + round(250 * i / (STEPS // 2))) for i in range(STEPS // 2)]
    for w, h in sizes + sizes[::-1]:
        times.append(pass_time(app, window, lambda w=w, h=h: window.resize(w, h)))
    results["resize window"] = stats(times)
    window.resize(1280, 800)
    app.processEvents()

    # (c) chat scroll, top to bottom
    bar = window.panel.view.verticalScrollBar()
    bar.setValue(0)
    app.processEvents()
    times = []
    start = time.perf_counter()
    value = 0
    while value < bar.maximum():
        value = min(bar.maximum(), value + 120)
        times.append(pass_time(app, window, lambda v=value: bar.setValue(v)))
    results["scroll chat"] = stats(times) | {"end_to_end_ms": round((time.perf_counter() - start) * 1000),
                                             "rows": window.panel.view.chat_model.rowCount()}

    # (d) screenshots
    shots = None
    if not args.no_shots:
        shots = Path(tempfile.mkdtemp(prefix="np_shots_"))
        from neuroplan.ui_qt.app import VIEW_NAMES
        for theme in config.THEME_NAMES:
            window.set_theme(theme, store=False)
            for w, h in SIZES:
                window.resize(w, h)
                for view in VIEW_NAMES:
                    window.show_view(view)
                    for _ in range(3):
                        app.processEvents()
                    window.grab().save(str(shots / f"{theme.replace(' & ', '_')}_{w}x{h}_{view}.png"))
        window.set_theme(config.THEME_NAMES[0], store=False)
        results["screenshots"] = {"count": len(list(shots.glob("*.png"))), "folder": str(shots)}

    window._close_asked = True
    window.close()
    repo.close()
    shutil.rmtree(tmp, ignore_errors=True)
    for name, value in results.items():
        print(json.dumps({name: value}) if args.json else f"{name:24s} {value}")
    if shots is not None and not args.keep:
        shutil.rmtree(shots, ignore_errors=True)
        print("screenshots deleted (use --keep to look at them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
