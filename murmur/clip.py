"""Clipboard snapshot/restore across formats (text, HTML, RTF, images, file lists), so a paste or a ctrl+c probe doesn't
destroy what the user copied. pywin32's win32clipboard is imported lazily; the pure parts are testable anywhere."""
import struct, time

CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE, CF_HDROP, CF_UNICODETEXT = 2, 3, 9, 14, 15, 13
CF_OWNERDISPLAY, CF_DSPTEXT, CF_DSPBITMAP, CF_DSPMETAFILEPICT, CF_DSPENHMETAFILE = 0x80, 0x81, 0x82, 0x83, 0x8E
SKIP = {CF_BITMAP, CF_METAFILEPICT, CF_PALETTE, CF_ENHMETAFILE, CF_OWNERDISPLAY, CF_DSPTEXT, CF_DSPBITMAP, CF_DSPMETAFILEPICT,
        CF_DSPENHMETAFILE}  # GDI handles that can't be round-tripped as bytes (the DIB formats cover images)
CAP = 64 * 1024 * 1024  # total bytes saved; above this the snapshot is abandoned (None) and restore is skipped
CF_DIB, CF_DIBV5 = 8, 17


def usable(fmt):
    """Can this clipboard format be saved as plain data? GDI object handles and owner-display formats can't."""
    return fmt not in SKIP and not 0x300 <= fmt <= 0x3FF


def size_of(data):
    return len(data.encode("utf-16-le")) if isinstance(data, str) else sum(len(x) for x in data) if isinstance(data, tuple) else len(data)


def within_cap(items, cap=None):
    """items = [(fmt, data)]: True if their total size fits the cap."""
    return sum(size_of(d) for _, d in items) <= (cap or CAP)


def build_hdrop(paths):
    """CF_HDROP payload (DROPFILES header + double-NUL wide list) for a tuple of file paths."""
    body = "".join(p + "\0" for p in paths).encode("utf-16-le") + b"\0\0"
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + body  # pFiles=20, pt=(0,0), fNC=0, fWide=1


def _open(cb, tries=10, wait=0.02):
    """OpenClipboard with retries: other apps hold it briefly."""
    for i in range(tries):
        try:
            cb.OpenClipboard()
            return True
        except Exception:
            if i == tries - 1:
                return False
            time.sleep(wait)
    return False


def _read(cb, fmt):
    try:
        return cb.GetClipboardData(fmt)
    except Exception:
        return None


def _shrink(cb, items):
    """Over the cap: drop the synthesized CF_DIB when a richer image format (CF_DIBV5 or a registered PNG) is also there.
    Returns the smaller list, or None if it still doesn't fit."""
    def is_png(f):
        try:
            return f >= 0xC000 and cb.GetClipboardFormatName(f) == "PNG"
        except Exception:
            return False
    if any(f == CF_DIBV5 or is_png(f) for f, _ in items):
        items = [(f, d) for f, d in items if f != CF_DIB]
    return items if within_cap(items) else None


def snapshot(cb=None):
    """Every saveable format on the clipboard as [(fmt, data)]. [] means the clipboard really was empty; None means "don't
    touch it afterwards": it couldn't be opened or read, nothing on it can be saved, or it is over the size cap even after
    dropping the synthesized DIB. Never raises."""
    try:
        cb = cb or __import__("win32clipboard")
    except ImportError:
        return None
    if not _open(cb):
        return None
    try:
        if cb.CountClipboardFormats() == 0:
            return []
        items, fmt = [], 0
        while True:
            fmt = cb.EnumClipboardFormats(fmt)
            if not fmt:
                break
            data = _read(cb, fmt) if usable(fmt) else None
            if data is not None:
                items.append((fmt, data))
        if not items:
            return None  # only GDI-handle formats: can't round-trip them, so leave the clipboard alone
        return items if within_cap(items) else _shrink(cb, items)
    except Exception:
        return None
    finally:
        cb.CloseClipboard()


def restore(items, cb=None):
    """Put a snapshot back in one clipboard session ([] = it was empty: clear it; None = no usable snapshot: do nothing).
    Formats that refuse are skipped."""
    if items is None:
        return
    try:
        cb = cb or __import__("win32clipboard")
    except ImportError:
        return
    if not _open(cb):
        return
    try:
        cb.EmptyClipboard()
        for fmt, data in items:
            try:
                cb.SetClipboardData(fmt, build_hdrop(data) if fmt == CF_HDROP and isinstance(data, tuple) else data)
            except Exception:
                continue
    finally:
        cb.CloseClipboard()


PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")


def set_text(text, cb=None):
    """Put `text` on the clipboard flagged so Windows keeps it out of Win+V history, cloud sync and clipboard monitors (it is
    only there for a moment, to be pasted). False if the clipboard couldn't be opened."""
    try:
        cb = cb or __import__("win32clipboard")
    except ImportError:
        return False
    if not _open(cb):
        return False
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(CF_UNICODETEXT, text)
        for name in PRIVATE_FORMATS:
            try:
                cb.SetClipboardData(cb.RegisterClipboardFormat(name), struct.pack("<I", 0))
            except Exception:
                continue  # an older Windows without the format: the text is still set
        return True
    except Exception:
        return False
    finally:
        cb.CloseClipboard()
