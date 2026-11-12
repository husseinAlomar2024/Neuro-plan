"""Windows DPI awareness. Must run before the first Tk window exists.

CustomTkinter makes the process per-monitor DPI aware itself, but only inside ``CTk.__init__``, after
Tk has already started. Tk then keeps the 96-DPI scaling it measured at start-up (``tk scaling`` 1.33
instead of 1.67 at 125 %), so everything Tk sizes in points is mis-scaled. Calling this first fixes that,
for ``python main.py`` and for the PyInstaller exe alike (both import this before any window)."""
from __future__ import annotations

import sys

PER_MONITOR = 2          # PROCESS_PER_MONITOR_DPI_AWARE (what CustomTkinter asks for, just earlier)


def enable_dpi_awareness() -> bool:
    """Make the process per-monitor DPI aware. Returns True when the process is per-monitor aware."""
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(PER_MONITOR)   # fails harmlessly when already set
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()               # Windows 7 fallback: system aware
        except (AttributeError, OSError):
            return False
    return process_awareness() == PER_MONITOR


def process_awareness() -> int:
    """0 unaware, 1 system aware, 2 per-monitor aware (-1 when unknown or not on Windows)."""
    if not sys.platform.startswith("win"):
        return -1
    import ctypes
    value = ctypes.c_int(-1)
    try:
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
    except (AttributeError, OSError):
        return -1
    return int(value.value)
