"""Dev tool: profile Qt layout+paint passes (coach splitter drag, window resize) with cProfile.

    .venv\\Scripts\\python tools\\profile_qt.py [splitter|resize|scroll] [--view Today]
"""
from __future__ import annotations

import cProfile
import pstats
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ui_check  # noqa: E402

from neuroplan import config  # noqa: E402
from neuroplan.db.repository import Repository  # noqa: E402
from neuroplan.seed import seed_demo  # noqa: E402


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "splitter"
    view = sys.argv[sys.argv.index("--view") + 1] if "--view" in sys.argv else "Today"
    from neuroplan.ui_qt.app import MainWindow, ensure_app
    app = ensure_app()
    repo = Repository(Path(tempfile.mkdtemp()) / "p.db", fast=True)
    seed_demo(repo, date.today())
    ui_check.fill_chat(repo)
    window = MainWindow(repo, run_startup=False)
    window.resize(1280, 800)
    window.show()
    window.show_view(view)
    for _ in range(30):
        app.processEvents()
        time.sleep(0.01)
    split = window.splitter
    total = sum(split.sizes())
    profiler = cProfile.Profile()
    times = []
    for i in range(30):
        if mode == "splitter":
            pos = total - config.COACH_MIN_WIDTH - 5 - i * 8
            change = (lambda p=pos: split.moveSplitter(p, 1))
        elif mode == "resize":
            change = (lambda k=i: window.resize(1100 + k * 10, 760))
        else:
            bar = window.panel.view.verticalScrollBar()
            change = (lambda k=i: bar.setValue(k * 120))
        profiler.enable()
        times.append(ui_check.pass_time(app, window, change))
        profiler.disable()
    print("pass ms:", [round(t) for t in times])
    stats = pstats.Stats(profiler)
    stats.sort_stats("tottime").print_stats(14)
    stats.sort_stats("cumulative").print_stats("neuroplan", 14)
    window._close_asked = True
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
