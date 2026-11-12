"""Dev tool (Windows only): real divider drags on the running app, checked on screen.

Launches NeuroPlan from source on a temporary seeded database with a dozen chat messages, then for each
scenario moves the real mouse (SetCursorPos + mouse_event), captures the NeuroPlan window's client
rectangle as fast as GDI allows (BitBlt from the screen, i.e. what the user sees) from just before the
release until 2.5 s after it, and reports:

  settle_ms   time from release to the first frame identical to the final settled frame
  broken      captured frames after release that are neither the pre-release image nor the final image
  ghosts      the largest number of ghost lines seen in one frame during the drag (should be 1)
  blank       whether the chat message list is blank at the end
  scroll_ok   whether the same message is still at the same edge (bottom stays bottom)

Captures live in memory; with --save the unique frames of each scenario are written as PNG files to a
temporary folder, which is deleted at the end unless --keep is given. Only the NeuroPlan window
rectangle is ever captured. No dependency beyond the standard library (ctypes/GDI).

    .venv\\Scripts\\python tools\\drag_check.py [--scenario NAME ...] [--save] [--keep] [--theme NAME]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import queue
import shutil
import struct
import sys
import tempfile
import threading
import time
import zlib
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if not sys.platform.startswith("win"):
    raise SystemExit("drag_check needs Windows (GDI screen capture and SendInput-style mouse input)")

from neuroplan.ui import dpi  # noqa: E402  (makes the process per-monitor DPI aware first)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
                         ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetParent.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
MOUSE_DOWN, MOUSE_UP = 0x0002, 0x0004
WM_SYSCOMMAND, SC_MAXIMIZE, SC_RESTORE = 0x0112, 0xF030, 0xF120   # what the title-bar buttons send
WATCH_S = 2.5


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class Grabber:
    """Copies a screen rectangle (physical px) into a reusable buffer, BGRA top-down."""

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self.screen = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(self.screen)
        self.bmp = gdi32.CreateCompatibleBitmap(self.screen, w, h)
        gdi32.SelectObject(self.mem, self.bmp)
        self.info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.buf = ctypes.create_string_buffer(w * h * 4)

    def grab(self) -> bytes:
        gdi32.BitBlt(self.mem, 0, 0, self.w, self.h, self.screen, self.x, self.y, SRCCOPY | CAPTUREBLT)
        gdi32.GetDIBits(self.mem, self.bmp, 0, self.h, self.buf, ctypes.byref(self.info), 0)
        return self.buf.raw

    def close(self) -> None:
        gdi32.DeleteObject(self.bmp)
        gdi32.DeleteDC(self.mem)
        user32.ReleaseDC(None, self.screen)


class Recorder(threading.Thread):
    """Captures frames until stopped: [(time, hash)], plus every distinct image once."""

    def __init__(self, grabber: Grabber) -> None:
        super().__init__(daemon=True)
        self.grabber = grabber
        self.frames: list[tuple[float, int]] = []
        self.images: dict[int, bytes] = {}
        self._stop_flag = threading.Event()

    def run(self) -> None:
        while not self._stop_flag.is_set():
            t = time.perf_counter()
            data = self.grabber.grab()
            key = zlib.crc32(data)
            self.frames.append((t, key))
            if key not in self.images:
                self.images[key] = data

    def stop(self) -> None:
        self._stop_flag.set()
        self.join()


def write_png(path: Path, w: int, h: int, bgra: bytes, half: bool = True) -> None:
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    if half:
        rows = [_half_row(rgb[r * w * 3:(r + 1) * w * 3]) for r in range(0, h, 2)]
        w, h = (w + 1) // 2, len(rows)
    else:
        rows = [bytes(rgb[r * w * 3:(r + 1) * w * 3]) for r in range(h)]
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _half_row(row: bytearray) -> bytes:
    """Every other RGB pixel of a row."""
    n = (len(row) // 3 + 1) // 2
    out = bytearray(n * 3)
    out[0::3], out[1::3], out[2::3] = row[0::6], row[1::6], row[2::6]
    return bytes(out)


def ghost_lines(bgra: bytes, w: int, h: int, color: str) -> int:
    """Clusters of columns that are the accent colour over most of the height (the ghost line)."""
    b, g, r = int(color[5:7], 16), int(color[3:5], 16), int(color[1:3], 16)
    target = bytes((b, g, r))
    hits = []
    sample_rows = range(h // 10, h - h // 10, max(1, h // 60))
    for x in range(w):
        count = sum(1 for y in sample_rows if bgra[(y * w + x) * 4:(y * w + x) * 4 + 3] == target)
        hits.append(count >= 0.7 * len(sample_rows))
    clusters, previous = 0, False
    for hit in hits:
        if hit and not previous:
            clusters += 1
        previous = hit
    return clusters


def distinct_colours(bgra: bytes, w: int, rect: tuple[int, int, int, int]) -> int:
    x0, y0, x1, y1 = rect
    seen = set()
    for y in range(max(0, y0), y1, 3):
        for x in range(max(0, x0), x1, 3):
            i = (y * w + x) * 4
            seen.add(bgra[i:i + 3])
    return len(seen)


# ---------------------------------------------------------------- the app, driven from a worker thread
class Harness:
    def __init__(self, theme_name: str) -> None:
        from neuroplan.db.repository import Repository
        from neuroplan.seed import seed_demo
        from neuroplan.ui.app import NeuroPlanApp
        self.tmp = Path(tempfile.mkdtemp(prefix="np_dragcheck_"))
        self.repo = Repository(self.tmp / "check.db", fast=True)
        seed_demo(self.repo, date.today())
        self.repo.set_setting("theme", theme_name)
        self.app = NeuroPlanApp(self.repo, run_startup=False)
        self.app.geometry("1280x820+40+30")
        self.app.attributes("-topmost", True)
        self.jobs: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._add_messages()
        self.app.after(10, self._pump)

    def _add_messages(self) -> None:
        from neuroplan.models import ChatMessage
        long = ("Here is the plan for this week. Machine Learning needs two catch-up blocks because you are three "
                "units behind, and the midterm is in nine days, so those come first in your peak window. ")
        texts = [("user", "What now?"), ("coach", "Start with Machine Learning, unit 4 (50 min)."),
                 ("user", "Only 2h today"), ("coach", long * 2), ("system_event", "Plan updated: 3 blocks moved"),
                 ("user", "Why this order?"), ("coach", long), ("user", "I'm 2 lectures behind in Statistics"),
                 ("coach", "Noted: Statistics is 2 units behind. " + long), ("user", "Rebalance week"),
                 ("coach", "Done. The week is rebalanced."), ("coach", "You skipped a block. What happened?")]
        for i, (role, text) in enumerate(texts):
            actions: dict[str, Any] = {}
            if i == 10:
                actions = {"actions": [{"tool": "regenerate_plan", "ok": True, "summary": "Week re-planned",
                                        "action_id": 1, "write": True}]}
            if i == 11:
                actions = {"options": [{"label": label, "payload": {"type": "noop"}} for label in
                                       ("Too tired", "Too hard", "No time", "Sick")]}
            self.app.panel.add_message(ChatMessage(1000 + i, "", role, text, actions))

    def _pump(self) -> None:
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            job()
        self.app.after(5, self._pump)

    def call(self, fn: Callable[[], Any], timeout: float = 60) -> Any:
        done = threading.Event()
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - handed back to the worker
                box["error"] = exc
            finally:
                done.set()
        self.jobs.put(run)
        if not done.wait(timeout):
            raise TimeoutError("the UI thread did not answer")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # ---------------------------------------------------------------- state read on the UI thread
    def client_rect(self) -> tuple[int, int, int, int]:
        a = self.app
        return a.winfo_rootx(), a.winfo_rooty(), a.winfo_width(), a.winfo_height()

    def chat_state(self) -> dict[str, Any]:
        panel = self.app.panel
        canvas = panel.messages._parent_canvas  # noqa: SLF001
        top, height = canvas.winfo_rooty(), canvas.winfo_height()
        children = panel.messages.winfo_children()
        visible = [i for i, w in enumerate(children)
                   if w.winfo_rooty() - top < height and w.winfo_rooty() - top + w.winfo_height() > 0]
        first, view = canvas.yview()
        return {"n": len(children), "visible": visible, "at_bottom": view >= 0.999, "yview": (first, view),
                "list": (canvas.winfo_rootx(), top, canvas.winfo_rootx() + canvas.winfo_width(), top + height),
                "panel_width": panel.width}

    def divider_point(self, which: str) -> tuple[int, int]:
        divider = self.app.coach_divider if which == "coach" else self.app.views["Subjects"].divider
        return (divider.winfo_rootx() + divider.winfo_width() // 2,
                divider.winfo_rooty() + divider.winfo_height() // 2)

    def settle(self, seconds: float = 1.2) -> None:
        self.call(lambda: self.app.update_idletasks())
        time.sleep(seconds)

    def close(self) -> None:
        self.call(self.app.destroy)


def analyse(name: str, rec: Recorder, t_release: float, grab: Grabber, before: dict[str, Any],
            after: dict[str, Any], accent: str, save_dir: Optional[Path]) -> dict[str, Any]:
    frames = rec.frames
    pre = [f for f in frames if f[0] < t_release]
    post = [f for f in frames if f[0] >= t_release]
    pre_key = pre[-1][1] if pre else None
    final_key = frames[-1][1]
    settle = next((f[0] for f in post if f[1] == final_key), frames[-1][0])
    broken = [f for f in post if f[1] not in (pre_key, final_key)]
    ghosts = max((ghost_lines(rec.images[k], grab.w, grab.h, accent) for k in {f[1] for f in pre}), default=0)
    lx0, ly0, lx1, ly1 = after["list"]
    rect = (lx0 - grab.x, ly0 - grab.y, lx1 - grab.x, ly1 - grab.y)
    colours = distinct_colours(rec.images[final_key], grab.w, rect)
    if before["at_bottom"]:
        scroll_ok = after["at_bottom"] and bool(after["visible"]) and after["visible"][-1] == after["n"] - 1
    else:
        scroll_ok = bool(after["visible"]) and after["visible"][0] == before["visible"][0]
    result = {"scenario": name, "settle_ms": round((settle - t_release) * 1000),
              "broken": len(broken), "distinct_broken": len({f[1] for f in broken}),
              "frames_after": len(post), "ghosts": ghosts, "blank": colours < 12 or not after["visible"],
              "scroll_ok": scroll_ok, "before": {k: before[k] for k in ("visible", "at_bottom", "panel_width")},
              "after": {k: after[k] for k in ("visible", "at_bottom", "panel_width")}}
    if save_dir is not None:
        folder = save_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        order: list[int] = []
        for _t, key in frames:
            if key not in order:
                order.append(key)
        for i, key in enumerate(order):
            tag = "pre" if key == pre_key else "final" if key == final_key else "broken"
            first_t = next(t for t, k in frames if k == key)
            write_png(folder / f"{i:03d}_{tag}_{round((first_t - t_release) * 1000):+05d}ms.png", grab.w, grab.h,
                      rec.images[key])
    return result


def run_drag(h: Harness, name: str, which: str, dx: int, setup: Optional[Callable[[], None]],
             accent: str, save_dir: Optional[Path]) -> dict[str, Any]:
    if setup is not None:
        h.call(setup)
    h.settle()
    before = h.call(h.chat_state)
    x, y = h.call(lambda: h.divider_point(which))
    grab = Grabber(*h.call(h.client_rect))
    user32.SetCursorPos(x, y)
    time.sleep(0.25)
    user32.mouse_event(MOUSE_DOWN, 0, 0, 0, 0)
    time.sleep(0.1)
    rec = Recorder(grab)
    rec.start()
    steps = 30
    for i in range(1, steps + 1):
        user32.SetCursorPos(x + round(dx * i / steps), y)
        time.sleep(0.012)
    time.sleep(0.3)
    t_release = time.perf_counter()
    user32.mouse_event(MOUSE_UP, 0, 0, 0, 0)
    time.sleep(WATCH_S)
    rec.stop()
    after = h.call(h.chat_state)
    result = analyse(name, rec, t_release, grab, before, after, accent, save_dir)
    grab.close()
    user32.SetCursorPos(x - 400, y + 300)          # off the divider: no hover highlight in the next scenario
    return result


def run_window(h: Harness, name: str, show: int, accent: str, save_dir: Optional[Path],
               rect: tuple[int, int, int, int]) -> dict[str, Any]:
    h.settle()
    before = h.call(h.chat_state)
    frame = h.call(lambda: int(h.app.wm_frame(), 16))
    grab = Grabber(*rect)
    rec = Recorder(grab)
    rec.start()
    time.sleep(0.3)
    t_release = time.perf_counter()
    user32.PostMessageW(frame, WM_SYSCOMMAND, show, 0)
    time.sleep(WATCH_S)
    rec.stop()
    after = h.call(h.chat_state)
    result = analyse(name, rec, t_release, grab, before, after, accent, save_dir)
    grab.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", nargs="*", default=None)
    parser.add_argument("--save", action="store_true", help="write the captured frames as PNG files")
    parser.add_argument("--keep", action="store_true", help="keep the PNG folder (it is deleted otherwise)")
    parser.add_argument("--theme", default="Midnight")
    args = parser.parse_args()
    assert dpi.process_awareness() == dpi.PER_MONITOR

    from neuroplan import config
    from neuroplan.ui import theme
    h = Harness(args.theme)
    accent = theme.THEMES[args.theme].colors["accent"]
    save_dir = Path(tempfile.mkdtemp(prefix="np_frames_")) if args.save else None
    results: list[dict[str, Any]] = []

    def coach_to(width_fn: Callable[[], int]) -> Callable[[], None]:
        return lambda: h.app.panel.set_width(width_fn())

    def at_bottom(width_fn: Callable[[], int]) -> Callable[[], None]:
        def setup() -> None:
            h.app.panel.set_width(width_fn())
            h.app.update_idletasks()
            h.app.update()
            h.app.panel.messages._parent_canvas.yview_moveto(1.0)  # noqa: SLF001
        return setup

    scenarios: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("coach_small", lambda: run_drag(h, "coach_small", "coach", -60, coach_to(lambda: 380), accent, save_dir)),
        ("coach_min_max", lambda: run_drag(h, "coach_min_max", "coach", -1400,
                                           coach_to(lambda: config.COACH_MIN_WIDTH), accent, save_dir)),
        ("coach_min_max_bottom", lambda: run_drag(h, "coach_min_max_bottom", "coach", -1400, at_bottom(
            lambda: config.COACH_MIN_WIDTH), accent, save_dir)),
        ("coach_max_min", lambda: run_drag(h, "coach_max_min", "coach", 1400,
                                           coach_to(lambda: h.app._coach_max_width()), accent, save_dir)),
        ("subjects", lambda: run_drag(h, "subjects", "subjects", -120,
                                      lambda: (h.app.panel.set_width(380), h.app.show_view("Subjects")),
                                      accent, save_dir)),
    ]

    def window_pair() -> list[dict[str, Any]]:
        h.call(lambda: (h.app.show_view("Today"), h.app.panel.set_width(380)))
        h.settle()
        rect = h.call(h.client_rect)
        return [run_window(h, "maximise", SC_MAXIMIZE, accent, save_dir, rect),
                run_window(h, "restore", SC_RESTORE, accent, save_dir, rect)]

    def worker() -> None:
        try:
            time.sleep(2.0)
            h.call(lambda: (h.app.show_view("Today"), h.app.lift(), h.app.focus_force()))
            for name, run in scenarios:
                if args.scenario and name not in args.scenario:
                    continue
                if name != "subjects":
                    h.call(lambda: h.app.show_view("Today"))
                results.append(run())
            if not args.scenario or "maximise" in args.scenario:
                results.extend(window_pair())
        except BaseException as exc:  # noqa: BLE001
            results.append({"error": repr(exc)})
        finally:
            h.jobs.put(h.app.destroy)

    threading.Thread(target=worker, daemon=True).start()
    h.app.mainloop()
    h.repo.close()
    shutil.rmtree(h.tmp, ignore_errors=True)
    for r in results:
        print(json.dumps(r))
    if save_dir is not None:
        if args.keep:
            print(f"frames kept in {save_dir}")
        else:
            shutil.rmtree(save_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
