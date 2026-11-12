"""Dev tool: measure the smallest centre-view width at which the Today view shows everything unclipped
(all four block buttons, KPI values, pomodoro buttons, block meta lines).

For each candidate width the window is sized so the centre view gets exactly that width, the layout is
settled, and every widget of the Today view is checked: it must lie inside its parent horizontally and
must not be narrower than it asks to be. Prints the smallest width that passes for each text size.

    .venv\\Scripts\\python tools\\centre_min.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from neuroplan import config  # noqa: E402
from neuroplan.db.repository import Repository  # noqa: E402
from neuroplan.seed import seed_demo  # noqa: E402
from neuroplan.ui import theme  # noqa: E402


def clipped(view: Any) -> list[str]:
    """Widgets of ``view`` that are cut off or squeezed below their requested width."""
    problems: list[str] = []

    def walk(widget: Any) -> None:
        for child in widget.winfo_children():
            if not child.winfo_ismapped() or child.winfo_class() in ("Scrollbar",):
                continue
            parent = child.nametowidget(child.winfo_parent())
            right = child.winfo_x() + child.winfo_width()
            if child.winfo_x() < -1 or right > parent.winfo_width() + 1:
                problems.append(f"{child} outside its parent ({right} > {parent.winfo_width()})")
            leaf = child.winfo_class() in ("Label", "Button", "Entry")
            if leaf and child.winfo_width() + 1 < child.winfo_reqwidth():
                problems.append(f"{child} squeezed ({child.winfo_width()} < {child.winfo_reqwidth()})")
            walk(child)
    walk(view)
    return problems


def centre_width_needed(app: Any, view_name: str = "Today") -> int:
    scale = app._scale()  # noqa: SLF001
    app.minsize(200, 200)
    app.show_view(view_name)
    best = None
    for width in range(900, 300, -10):
        total = theme.SIDEBAR_WIDTH + config.DIVIDER_WIDTH + app.panel.width + width
        app.geometry(f"{total}x820")
        app.settle_layout()
        app.update()
        app.settle_layout()
        actual = round(app.centre.winfo_width() / scale)
        if clipped(app.views[view_name]):
            break
        best = actual
    return best or 0


def main() -> int:
    from neuroplan.ui import fonts
    from neuroplan.ui.app import NeuroPlanApp
    tmp = Path(tempfile.mkdtemp(prefix="np_centre_"))
    repo = Repository(tmp / "m.db", fast=True)
    seed_demo(repo, date.today())
    for size in config.TEXT_SIZE_NAMES:
        repo.set_setting("text_size", size)
        app = NeuroPlanApp(repo, run_startup=False)
        app.update()
        print(size, fonts.text_size(), "centre min width:", centre_width_needed(app))
        app.destroy()
    repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
