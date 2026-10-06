"""UpdateLayeredWindow driver for the caption card (Windows only; ctypes/win32 imported on use)."""


import ctypes
from ctypes import wintypes as wt


class BF(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]


class BIH(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("w", wt.LONG), ("h", wt.LONG), ("planes", wt.WORD), ("bpp", wt.WORD), ("comp", wt.DWORD),
                ("img", wt.DWORD), ("xp", wt.LONG), ("yp", wt.LONG), ("used", wt.DWORD), ("imp", wt.DWORD)]


def _bind():
    """user32/gdi32 with handle-sized prototypes (64-bit safe)."""
    u, g, v = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True), ctypes.c_void_p
    u.GetDC.restype, u.GetDC.argtypes = v, [wt.HWND]
    g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = v, [v]
    g.CreateDIBSection.restype, g.CreateDIBSection.argtypes = v, [v, v, wt.UINT, v, v, wt.DWORD]
    g.SelectObject.restype, g.SelectObject.argtypes = v, [v, v]
    g.DeleteObject.argtypes = [v]
    u.UpdateLayeredWindow.argtypes = [wt.HWND, v, v, v, v, v, wt.DWORD, v, wt.DWORD]
    return u, g


class Layered:
    """UpdateLayeredWindow driver: owns a 32-bit top-down DIB section and pushes premultiplied PIL frames to one hwnd."""

    def __init__(self, hwnd):
        import win32gui
        from PIL import Image
        self.ct, self.wt, self.hwnd, self.size, self.im, self.BF = ctypes, wt, hwnd, None, None, BF
        self.u, self.g = _bind()
        # LAYERED | NOACTIVATE | TOOLWINDOW | TOPMOST: never takes focus, no taskbar entry, clickable where alpha > 0
        win32gui.SetWindowLong(hwnd, -20, win32gui.GetWindowLong(hwnd, -20) | 0x80000 | 0x08000000 | 0x80 | 0x8)
        self.screen = self.u.GetDC(None)
        self.mem = self.g.CreateCompatibleDC(self.screen)
        self.bmp = self.bits = None
        self.update(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), -32000, -32000, 0)  # raises when the system refuses

    def _alloc(self, w, h):
        ct = self.ct
        if self.bmp:
            self.g.DeleteObject(self.bmp)
        bits = ct.c_void_p()
        self.bmp = self.g.CreateDIBSection(self.mem, ct.byref(BIH(40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)), 0, ct.byref(bits), None, 0)
        if not self.bmp:
            raise ct.WinError(ct.get_last_error())
        self.g.SelectObject(self.mem, self.bmp)
        self.bits, self.size, self.im = bits.value, (w, h), None

    def _upload(self, im):
        """Copy `im` into the DIB (reallocating on a size change) unless it is already there."""
        if im.size != self.size:
            self._alloc(*im.size)
        if im is self.im:
            return
        data = im.convert("RGBa").tobytes("raw", "BGRa")  # premultiplied BGRA, what the DIB wants
        self.ct.memmove(self.bits, data, len(data))
        self.im = im

    def update(self, im, x, y, alpha):
        """Show RGBA `im` with its top-left at screen (x, y), constant opacity alpha 0-255 (the fade)."""
        self._upload(im)
        ct, wt = self.ct, self.wt
        args = (ct.byref(wt.POINT(x, y)), ct.byref(wt.SIZE(*im.size)), self.mem, ct.byref(wt.POINT(0, 0)), 0, ct.byref(self.BF(0, 0, alpha, 1)), 2)
        if not self.u.UpdateLayeredWindow(self.hwnd, None, *args):
            raise ct.WinError(ct.get_last_error())
