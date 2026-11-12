"""Small Win32 helpers for flicker-free layout changes (no-ops on other platforms).

* ``covered(window)``: while a layout change is applied, a snapshot of the window's client area is
  shown in a borderless popup directly above it. The window relayouts and repaints underneath (the
  window manager keeps rendering covered windows), and the cover is removed once everything is painted,
  so the user sees the old layout and then the new one, nothing in between.
  (Suspending painting with WM_SETREDRAW was tried first and does not work with Tk: Tk's child windows
  keep drawing, so half-finished layouts still reached the screen.)
* ``LineWindow``: a 2 px solid-colour popup, the ghost line of a divider drag. Being its own top-level
  window it is composited by the window manager, so moving it never leaves copies behind.
* ``MaximiseCover``: puts the cover up before Windows maximises or restores the window, so Tk's relayout
  after the size change is not shown half done either.
"""
from __future__ import annotations

import contextlib
import sys
from typing import Any, Callable, Iterator, Optional

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    _user32.SendMessageW.restype = _LRESULT
    _user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _user32.CreateWindowExW.restype = wt.HWND
    _user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    _user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wt.UINT]
    _user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    _user32.UpdateWindow.argtypes = [wt.HWND]
    _user32.DestroyWindow.argtypes = [wt.HWND]
    _user32.IsWindow.argtypes = [wt.HWND]
    _user32.GetWindow.restype = wt.HWND
    _user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    _user32.GetDC.restype = wt.HDC
    _user32.GetDC.argtypes = [wt.HWND]
    _user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    _user32.FillRect.argtypes = [wt.HDC, ctypes.c_void_p, wt.HBRUSH]
    _user32.GetClientRect.argtypes = [wt.HWND, ctypes.c_void_p]
    _user32.ClientToScreen.argtypes = [wt.HWND, ctypes.c_void_p]
    _user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, wt.BYTE, wt.DWORD]
    _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.CallWindowProcW.restype = _LRESULT
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _gdi32.CreateSolidBrush.restype = wt.HBRUSH
    _gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
    _gdi32.CreateCompatibleDC.restype = wt.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    _gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    _gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.SelectObject.restype = wt.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    _gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
                              ctypes.c_int, wt.DWORD]
    _gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wt.HDC]
    _kernel32.GetModuleHandleW.restype = wt.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

STM_SETIMAGE, IMAGE_BITMAP = 0x0172, 0
WS_POPUP, WS_DISABLED, SS_BITMAP = 0x80000000, 0x08000000, 0x0000000E
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x80, 0x08000000, 0x20, 0x80000
SWP_NOACTIVATE, SWP_NOZORDER, SWP_SHOWWINDOW = 0x0010, 0x0004, 0x0040
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
GW_HWNDPREV = 3
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
WM_SYSCOMMAND, SC_MAXIMIZE, SC_RESTORE = 0x0112, 0xF030, 0xF120
GWLP_WNDPROC = -4

Rect = tuple[int, int, int, int]          # screen px: x, y, width, height


def toplevel_hwnd(window: Any) -> Optional[int]:
    """The Win32 frame window of a Tk toplevel."""
    if not IS_WINDOWS:
        return None
    try:
        return int(window.wm_frame(), 16)
    except Exception:  # noqa: BLE001 - not mapped yet or already destroyed
        return None


def client_rect(hwnd: int) -> Rect:
    rect = wt.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wt.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


def _rgb(color: str) -> int:
    value = color.lstrip("#")
    return int(value[0:2], 16) | (int(value[2:4], 16) << 8) | (int(value[4:6], 16) << 16)


class _Popup:
    """An owned, borderless, never-activated STATIC window showing a bitmap."""

    def __init__(self, owner: int, click_through: bool) -> None:
        self.owner = owner
        self.click_through = click_through
        self.hwnd: Optional[int] = None
        self.bitmap: Optional[int] = None
        self.visible = False
        self.rect: Rect = (0, 0, 0, 0)

    def _ensure(self) -> int:
        if self.hwnd is None or not _user32.IsWindow(self.hwnd):
            ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
            if self.click_through:
                ex |= WS_EX_TRANSPARENT
            self.hwnd = _user32.CreateWindowExW(ex, "STATIC", None, WS_POPUP | WS_DISABLED | SS_BITMAP,
                                                0, 0, 1, 1, self.owner, None, _kernel32.GetModuleHandleW(None),
                                                None)
            _user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 0x2)     # LWA_ALPHA: fully opaque
        return int(self.hwnd)

    def set_bitmap(self, bitmap: int) -> None:
        _user32.SendMessageW(self._ensure(), STM_SETIMAGE, IMAGE_BITMAP, bitmap)
        if self.bitmap:
            _gdi32.DeleteObject(self.bitmap)
        self.bitmap = bitmap

    def place(self, rect: Rect) -> None:
        """Show at ``rect``, directly above the owner window (not above other applications)."""
        hwnd = self._ensure()
        above = _user32.GetWindow(self.owner, GW_HWNDPREV)
        if above == hwnd:
            above = None
        x, y, w, h = rect
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW | (0 if above else SWP_NOZORDER)
        _user32.SetWindowPos(hwnd, above, x, y, w, h, flags)
        if not self.visible:
            _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            self.visible = True
        _user32.UpdateWindow(hwnd)                 # painted now, before the caller goes on
        self.rect = rect

    def hide(self) -> None:
        if self.hwnd and self.visible:
            _user32.ShowWindow(self.hwnd, SW_HIDE)
        self.visible = False

    def destroy(self) -> None:
        self.hide()
        if self.hwnd and _user32.IsWindow(self.hwnd):
            _user32.DestroyWindow(self.hwnd)
        if self.bitmap:
            _gdi32.DeleteObject(self.bitmap)
        self.hwnd = None
        self.bitmap = None


def _new_bitmap(w: int, h: int, fill: Optional[str] = None,
                source: Optional[tuple[int, int, int]] = None) -> int:
    """A w x h bitmap, filled with ``fill`` and/or with ``source`` (bitmap, x, y) copied in at x, y.
    Without a source the screen is not read; with ``fill`` None and no source it copies nothing."""
    screen = _user32.GetDC(None)
    memory = _gdi32.CreateCompatibleDC(screen)
    bitmap = _gdi32.CreateCompatibleBitmap(screen, w, h)
    old = _gdi32.SelectObject(memory, bitmap)
    if fill is not None:
        brush = _gdi32.CreateSolidBrush(_rgb(fill))
        rect = wt.RECT(0, 0, w, h)
        _user32.FillRect(memory, ctypes.byref(rect), brush)
        _gdi32.DeleteObject(brush)
    if source is not None:
        src_bitmap, x, y = source
        src = _gdi32.CreateCompatibleDC(screen)
        src_old = _gdi32.SelectObject(src, src_bitmap)
        _gdi32.BitBlt(memory, x, y, w - max(0, x), h - max(0, y), src, 0, 0, SRCCOPY)
        _gdi32.SelectObject(src, src_old)
        _gdi32.DeleteDC(src)
    _gdi32.SelectObject(memory, old)
    _gdi32.DeleteDC(memory)
    _user32.ReleaseDC(None, screen)
    return int(bitmap)


def _screen_bitmap(rect: Rect) -> int:
    """What the screen shows in ``rect`` right now (layered popups such as the ghost line included)."""
    x, y, w, h = rect
    screen = _user32.GetDC(None)
    memory = _gdi32.CreateCompatibleDC(screen)
    bitmap = _gdi32.CreateCompatibleBitmap(screen, w, h)
    old = _gdi32.SelectObject(memory, bitmap)
    _gdi32.BitBlt(memory, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT)
    _gdi32.SelectObject(memory, old)
    _gdi32.DeleteDC(memory)
    _user32.ReleaseDC(None, screen)
    return int(bitmap)


class Cover:
    """A snapshot of a window's client area shown above it while the window relayouts underneath."""

    def __init__(self, owner: int) -> None:
        self.popup = _Popup(owner, click_through=False)
        self.owner = owner

    @property
    def visible(self) -> bool:
        return self.popup.visible

    def show(self) -> None:
        rect = client_rect(self.owner)
        if rect[2] <= 0 or rect[3] <= 0:
            return
        self.popup.set_bitmap(_screen_bitmap(rect))
        self.popup.place(rect)

    def follow(self, background: str) -> None:
        """The window changed size under the cover: cover the new client area too (old picture where it
        was, the background colour around it), so nothing half laid out shows in the new area."""
        if not self.popup.visible or self.popup.bitmap is None:
            return
        new = client_rect(self.owner)
        old = self.popup.rect
        if new == old or new[2] <= 0 or new[3] <= 0:
            return
        bitmap = _new_bitmap(new[2], new[3], background, (self.popup.bitmap, old[0] - new[0], old[1] - new[1]))
        self.popup.set_bitmap(bitmap)
        self.popup.place(new)

    def hide(self) -> None:
        self.popup.hide()

    def destroy(self) -> None:
        self.popup.destroy()


@contextlib.contextmanager
def covered(window: Any, cover: Optional[Cover]) -> Iterator[None]:
    """Keep ``cover`` (showing the current picture) above ``window`` while the block runs; it is always
    removed afterwards. Does nothing when the window is not on screen."""
    show = cover is not None
    if show:
        try:
            show = bool(window.winfo_viewable())
        except Exception:  # noqa: BLE001
            show = False
    if show and cover is not None:
        cover.show()
    try:
        yield
    finally:
        if show and cover is not None:
            cover.hide()


class LineWindow:
    """The ghost line: a solid-colour, click-through popup owned by ``owner_hwnd``."""

    def __init__(self, owner_hwnd: int) -> None:
        self.popup = _Popup(owner_hwnd, click_through=True)
        self._size = (0, 0)
        self._color = ""

    @property
    def visible(self) -> bool:
        return self.popup.visible

    @property
    def rect(self) -> Rect:
        return self.popup.rect

    def show(self, x: int, y: int, w: int, h: int, color: str) -> None:
        if not IS_WINDOWS:
            return
        if (w, h) != self._size or color != self._color or self.popup.bitmap is None:
            self.popup.set_bitmap(_new_bitmap(w, h, color))
            self._size, self._color = (w, h), color
        self.popup.place((x, y, w, h))

    def move(self, x: int, y: int) -> None:
        if IS_WINDOWS and self.popup.visible and self.popup.hwnd:
            _, _, w, h = self.popup.rect
            _user32.SetWindowPos(self.popup.hwnd, None, x, y, w, h, SWP_NOACTIVATE | SWP_NOZORDER)
            self.popup.rect = (x, y, w, h)

    def hide(self) -> None:
        if IS_WINDOWS:
            self.popup.hide()

    def destroy(self) -> None:
        if IS_WINDOWS:
            self.popup.destroy()


class MaximiseCover:
    """Puts ``cover`` up before Windows maximises or restores the window.

    The frame window's procedure is subclassed; on WM_SYSCOMMAND maximise/restore (title-bar button,
    double click on the title bar, system menu) the cover shows the current picture, Windows resizes the
    window, and the cover is extended to the new client area. The app settles the new layout underneath
    and calls ``release()``. If the client size did not change, the cover is removed at once. Only plain
    Python and Win32 calls run inside the hook (no Tcl)."""

    def __init__(self, hwnd: int, cover: Cover, background: Callable[[], str]) -> None:
        self.hwnd = hwnd
        self.cover = cover
        self.background = background
        self.pending = False
        self._old: Optional[int] = None
        self._proc = _WNDPROC(self._wndproc) if IS_WINDOWS else None
        if IS_WINDOWS and hwnd:
            self._old = _user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_SYSCOMMAND and (wparam & 0xFFF0) in (SC_MAXIMIZE, SC_RESTORE) and not self.pending:
            before = client_rect(hwnd)
            self.cover.show()
            self.pending = self.cover.visible
            try:
                return int(_user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam))
            finally:
                if client_rect(hwnd)[2:] == before[2:]:
                    self.release()
                else:
                    self.cover.follow(self.background())
        return int(_user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam))

    def release(self) -> None:
        if self.pending:
            self.pending = False
            self.cover.hide()

    def remove(self) -> None:
        self.release()
        if IS_WINDOWS and self._old and _user32.IsWindow(self.hwnd):
            _user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC, self._old)
        self._old = None
