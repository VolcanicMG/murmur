"""Windows glue: foreground app, clipboard paste/type, DPI, window placement helpers."""
import ctypes, os, time
from ctypes import wintypes
import keyboard, pyperclip, win32api, win32gui, win32process
from . import clip
from .theme import FILL, TEXT


_mutex = None  # kept for the process lifetime


def single_instance():
    """True if this is the only murmur tray app; else tells the user (native box) and returns False. Always True off Windows."""
    global _mutex
    if os.name != "nt":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = k32.CreateMutexW(None, False, "Local\\murmur-single-instance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(None, "murmur is already running. Look for the bubble in the system tray.", "murmur", 0x40)
        return False
    return True


def active_app():
    """(lowercase process exe name, window title) of the foreground window."""
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    try:
        h = win32api.OpenProcess(0x0410, False, win32process.GetWindowThreadProcessId(hwnd)[1])
        exe = win32process.GetModuleFileNameEx(h, 0).rsplit("\\", 1)[-1].lower()
        win32api.CloseHandle(h)
    except Exception:
        exe = ""
    return exe, title


clip_snapshot, clip_restore = clip.snapshot, clip.restore  # every format, not just text


def quns_state():
    """SHQueryUserNotificationState (3 = a D3D exclusive-fullscreen app is in front); 0 if unavailable."""
    try:
        state = ctypes.c_int(0)
        return state.value if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 else 0
    except Exception:
        return 0


def paste(text):
    snap = clip_snapshot()
    if not clip.set_text(text):  # flagged out of clipboard history / cloud sync; plain pyperclip if that fails
        pyperclip.copy(text)
    keyboard.send("ctrl+v")
    time.sleep(0.4)  # slow targets read the clipboard late
    clip_restore(snap)


def insert(text, cfg):
    """Output sink: cfg["output"]["method"] = "paste" (default) or "type" (keyboard.write, for paste-blocking apps)."""
    if cfg.get("output", {}).get("method", "paste") == "type":
        for i, line in enumerate(text.split("\n")):  # a bare newline would send (Enter) in chat apps
            if i:
                keyboard.send("shift+enter")
            keyboard.write(line)
    else:
        paste(text)


def undo():
    keyboard.send("ctrl+z")


def copy_selection():
    """Selected text via ctrl+c, or None if nothing was copied."""
    snap = clip_snapshot()
    pyperclip.copy("")
    keyboard.send("ctrl+c")
    time.sleep(0.3)
    got = pyperclip.paste()
    clip_restore(snap)
    return got or None


def to_clipboard(text):
    """Leave our text on the clipboard (a failed delivery), flagged out of Win+V history and cloud sync."""
    if not clip.set_text(text):
        pyperclip.copy(text)


def dpi_aware():
    """Real-pixel rendering, per-monitor v2 (call first thing in main, before any window): no bitmap-stretched blur.

    ponytail: Tk 8.6 doesn't follow WM_DPICHANGED, so scale is read once from the system DPI; mixed-DPI monitors
    would need GetDpiForWindow per move.
    """
    try:
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def dpi_scale(root=None):
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96
    except Exception:
        return 1.0


def foreground():
    return win32gui.GetForegroundWindow()


def refocus(hwnd):
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def no_activate(root):
    """NOACTIVATE|TOOLWINDOW: clicks and showing never take focus from the target field; no taskbar entry."""
    hwnd = win32gui.GetParent(root.winfo_id()) or root.winfo_id()
    win32gui.SetWindowLong(hwnd, -20, win32gui.GetWindowLong(hwnd, -20) | 0x08000000 | 0x80)


def work_area(point):
    """(l, t, r, b) work area of the monitor containing point (None = primary)."""
    return win32api.GetMonitorInfo(win32api.MonitorFromPoint(point or (0, 0), 2))["Work"]


class _GTI(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD)] + [
        (n, wintypes.HWND) for n in ("hwndActive", "hwndFocus", "hwndCapture", "hwndMenuOwner", "hwndMoveSize", "hwndCaret")
    ] + [("rcCaret", wintypes.RECT)]


def focus_hwnd():
    """Focused window handle of the foreground thread (falls back to the foreground window)."""
    try:
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        gti = _GTI(); gti.cbSize = ctypes.sizeof(_GTI)
        user32.GetGUIThreadInfo(user32.GetWindowThreadProcessId(fg, None), ctypes.byref(gti))
        return gti.hwndFocus or fg
    except Exception:
        return None


def caret_rect():
    """Screen rect of the foreground thread's caret, or None (many modern apps don't expose one)."""
    try:
        user32 = ctypes.windll.user32
        tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        gti = _GTI(); gti.cbSize = ctypes.sizeof(_GTI)
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)) or not gti.hwndCaret:
            return None
        r = gti.rcCaret
        l, t = win32gui.ClientToScreen(gti.hwndCaret, (r.left, r.top))
        rr, b = win32gui.ClientToScreen(gti.hwndCaret, (r.right, r.bottom))
        return (l, t, max(rr, l + 1), b)
    except Exception:
        return None


def style_window(top):
    """Dark title bar and rounded corners (Windows 11); silently ignored where unsupported."""
    try:
        top.update_idletasks()  # the frame hwnd only exists once the window is realised
        hwnd = win32gui.GetParent(top.winfo_id()) or top.winfo_id()
        bgr = lambda h: int(h[5:7] + h[3:5] + h[1:3], 16)  # COLORREF is 0x00BBGGRR
        # IMMERSIVE_DARK_MODE, WINDOW_CORNER_PREFERENCE=ROUND, CAPTION_COLOR=card fill, TEXT_COLOR
        for attr, val in ((20, 1), (33, 2), (35, bgr(FILL)), (36, bgr(TEXT))):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_int(val)), 4)
    except Exception:
        pass
