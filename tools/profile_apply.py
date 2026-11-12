"""Dev tool: profile the atomic layout pass that runs when a divider is released.

Launches the app on a temporary seeded database (same chat as drag_check), applies several coach
widths through ``app.apply_layout`` exactly like a divider release does, and prints the wall time per
phase plus the top functions by own time.

    .venv\\Scripts\\python tools\\profile_apply.py [--cprofile]
"""
from __future__ import annotations

import cProfile
import pstats
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drag_check  # noqa: E402

PHASES: dict[str, float] = defaultdict(float)


def timed(owner: Any, name: str, label: str) -> None:
    original: Callable[..., Any] = getattr(owner, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            PHASES[label] += (time.perf_counter() - start) * 1000
    setattr(owner, name, wrapper)


def main() -> int:
    harness = drag_check.Harness("Midnight")
    app = harness.app
    app.deiconify()
    for _ in range(20):
        app.update()
        time.sleep(0.03)
    cover = app.layout_cover()
    assert cover is not None
    timed(cover, "show", "cover.show (snapshot)")
    timed(cover, "hide", "cover.hide")
    timed(app, "update_idletasks", "update_idletasks (geometry + <Configure>)")
    timed(app, "_drain_events", "drain events (redraws, paints)")
    timed(app.panel, "rewrap_now", "chat re-wrap")
    timed(app.panel, "restore_scroll", "scroll restore")
    timed(app.panel, "set_width", "set_width")
    widths = [440, 380, 300, 396, 380, 330]
    profiler = cProfile.Profile()
    totals = []
    for width in widths:
        if "--cprofile" in sys.argv:
            profiler.enable()
        app.apply_layout(lambda w=width: app.panel.set_width(w))
        if "--cprofile" in sys.argv:
            profiler.disable()
        totals.append(round(app.layout_ms))
        app.update()
        time.sleep(0.2)
    print("apply_layout ms per run:", totals, " mean", round(sum(totals) / len(totals)))
    for label, ms in sorted(PHASES.items(), key=lambda item: -item[1]):
        print(f"  {label:45s} {ms / len(widths):7.1f} ms per apply")
    if "--cprofile" in sys.argv:
        stats = pstats.Stats(profiler)
        stats.sort_stats("tottime").print_stats(15)
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
