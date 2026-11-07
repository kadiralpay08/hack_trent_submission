"""Turn the camera window into a small always-on-top popup in a corner of the screen (Windows only).

It is the same picture the tracker always shows, but the window has no title bar, sits above the game,
never takes keyboard focus (so key presses keep going to the game), stays out of the taskbar and Alt+Tab,
and by default lets mouse clicks fall through to whatever is under it. Because it can't be clicked, quit
with the F9 hotkey.

Use it through punch_live.py --popup. A game in exclusive full screen hides it; use the game's windowed or
borderless full screen mode.
"""
import sys

WINDOW = "Pose"          # the camera window pose_tracker.py creates
EXTRA_WINDOW = "3D skeleton"  # a second tracker window, hidden while the popup is on
CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")


def corner_rect(work, size, corner="bottom-right", margin=16):
    """Where a window of size (w, h) goes in the work area (left, top, right, bottom): (x, y, w, h)."""
    left, top, right, bottom = work
    w, h = size
    x = right - w - margin if corner.endswith("right") else left + margin
    y = bottom - h - margin if corner.startswith("bottom") else top + margin
    return x, y, w, h


class Popup:
    def __init__(self, width=360, corner="bottom-right", opacity=0.92, click_through=True, margin=16):
        if corner not in CORNERS:
            raise ValueError(f"corner must be one of {CORNERS}")
        self.width, self.corner, self.margin = width, corner, margin
        self.alpha = int(255 * min(1.0, max(0.2, opacity)))
        self.click_through = click_through
        self.styled, self.frames = False, 0
        self.enabled = sys.platform == "win32"
        if self.enabled:
            self._load()

    def _load(self):
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = wintypes.HWND
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL
        u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
        u.SetLayeredWindowAttributes.restype = wintypes.BOOL
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        self.user32, self.rect_type = u, wintypes.RECT

    def _work_area(self):
        import ctypes
        rect = self.rect_type()
        self.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA: screen minus taskbar
        return rect.left, rect.top, rect.right, rect.bottom

    def update(self, frame):
        """Call once per frame, before the frame is shown. Does the work only the first time and then rarely."""
        if not self.enabled:
            return
        self.frames += 1
        u = self.user32
        hwnd = u.FindWindowW(None, WINDOW)
        if hwnd and not self.styled:
            self._style(hwnd, frame.shape[1], frame.shape[0])
        elif hwnd and self.frames % 60 == 0:
            u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)  # keep above a game that took topmost
        extra = u.FindWindowW(None, EXTRA_WINDOW)
        if extra and u.IsWindowVisible(extra):
            u.ShowWindow(extra, 0)  # SW_HIDE

    def _style(self, hwnd, frame_w, frame_h):
        u = self.user32
        GWL_STYLE, GWL_EXSTYLE = -16, -20
        style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        style &= ~(0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000)  # caption, frame, min, max, menu
        u.SetWindowLongPtrW(hwnd, GWL_STYLE, style | 0x80000000 | 0x10000000)  # WS_POPUP | WS_VISIBLE
        ex = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        ex &= ~0x00040000  # WS_EX_APPWINDOW: no taskbar button
        ex |= 0x00000008 | 0x08000000 | 0x00000080 | 0x00080000  # topmost, no-activate, tool window, layered
        if self.click_through:
            ex |= 0x00000020  # WS_EX_TRANSPARENT: clicks pass through to the window below
        u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        u.SetLayeredWindowAttributes(hwnd, 0, self.alpha, 0x2)  # LWA_ALPHA
        size = (self.width, max(1, round(self.width * frame_h / frame_w)))
        x, y, w, h = corner_rect(self._work_area(), size, self.corner, self.margin)
        # HWND_TOPMOST, and SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW
        u.SetWindowPos(hwnd, -1, x, y, w, h, 0x0010 | 0x0020 | 0x0040)
        self.styled = True
