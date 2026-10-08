import os, struct, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import clip

HTML = 49300  # a registered format id (value is arbitrary in the fake)


class FakeCB:
    """win32clipboard stand-in: an ordered dict of format -> data; formats in `bad` raise on read."""
    def __init__(self, data=None, bad=(), busy=0):
        self.data, self.bad, self.busy, self.opened, self.sets, self.names = dict(data or {}), set(bad), busy, 0, [], {}

    def OpenClipboard(self):
        if self.busy:
            self.busy -= 1
            raise OSError("held by another app")
        self.opened += 1

    def CloseClipboard(self):
        self.opened -= 1

    def RegisterClipboardFormat(self, name):
        self.registered = getattr(self, "registered", {})
        return self.registered.setdefault(name, 50000 + len(self.registered))

    def CountClipboardFormats(self):
        return len(self.data)

    def GetClipboardFormatName(self, fmt):
        return self.names.get(fmt, "")

    def EnumClipboardFormats(self, fmt):
        keys = list(self.data)
        if fmt == 0:
            return keys[0] if keys else 0
        i = keys.index(fmt) + 1
        return keys[i] if i < len(keys) else 0

    def GetClipboardData(self, fmt):
        if fmt in self.bad:
            raise OSError("cannot read")
        return self.data[fmt]

    def EmptyClipboard(self):
        self.data = {}

    def SetClipboardData(self, fmt, data):
        if fmt in self.bad:
            raise OSError("cannot set")
        self.data[fmt] = data; self.sets.append(fmt)


class Pure(unittest.TestCase):
    def test_usable_skips_gdi_handles(self):
        for f in (clip.CF_BITMAP, clip.CF_ENHMETAFILE, clip.CF_METAFILEPICT, clip.CF_PALETTE, clip.CF_OWNERDISPLAY, 0x305):
            self.assertFalse(clip.usable(f), f)
        for f in (clip.CF_UNICODETEXT, 1, 8, 17, clip.CF_HDROP, HTML):
            self.assertTrue(clip.usable(f), f)

    def test_size_cap(self):
        self.assertTrue(clip.within_cap([(1, b"x" * 10)], cap=10))
        self.assertFalse(clip.within_cap([(1, b"x" * 10), (2, "ab")], cap=13))  # "ab" is 4 bytes as UTF-16
        self.assertEqual(clip.size_of(("a", "bc")), 3)

    def test_build_hdrop(self):
        d = clip.build_hdrop(("C:\\a.txt", "C:\\b.txt"))
        self.assertEqual(struct.unpack("<IiiII", d[:20]), (20, 0, 0, 0, 1))
        self.assertEqual(d[20:].decode("utf-16-le"), "C:\\a.txt\0C:\\b.txt\0\0")


class SetText(unittest.TestCase):  # R7
    def test_text_is_flagged_out_of_history_and_cloud(self):
        cb = FakeCB({clip.CF_UNICODETEXT: "old"})
        self.assertTrue(clip.set_text("dictated text", cb))
        self.assertEqual(cb.data[clip.CF_UNICODETEXT], "dictated text")
        names = set(cb.registered)
        self.assertEqual(names, set(clip.PRIVATE_FORMATS))
        for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
            self.assertEqual(cb.data[cb.registered[name]], struct.pack("<I", 0))  # DWORD 0
        self.assertIn(cb.registered["ExcludeClipboardContentFromMonitorProcessing"], cb.data)
        self.assertEqual(cb.opened, 0)

    def test_busy_clipboard_returns_false_and_changes_nothing(self):
        cb = FakeCB({clip.CF_UNICODETEXT: "old"}, busy=99)
        self.assertFalse(clip.set_text("x", cb))
        self.assertEqual(cb.data, {clip.CF_UNICODETEXT: "old"})


class FailedDelivery(unittest.TestCase):
    def test_to_clipboard_uses_the_history_excluded_path(self):
        import sys
        from unittest import mock
        stubs = {m: mock.MagicMock() for m in ("keyboard", "pyperclip", "win32api", "win32gui", "win32process")}
        with mock.patch.dict(sys.modules, stubs):
            from murmur import win
            with mock.patch.object(win.clip, "set_text", return_value=True) as st:
                win.to_clipboard("leftover text")
            st.assert_called_once_with("leftover text")
            win.pyperclip.copy.assert_not_called()
            with mock.patch.object(win.clip, "set_text", return_value=False):  # clipboard busy: plain copy as a last resort
                win.to_clipboard("x")
            win.pyperclip.copy.assert_called_once_with("x")


class Snapshot(unittest.TestCase):
    def test_round_trip_keeps_every_saveable_format(self):
        cb = FakeCB({clip.CF_UNICODETEXT: "héllo", HTML: b"<b>hi</b>", 8: b"dib-bytes", clip.CF_BITMAP: object(), 0x305: b"gdi"})
        snap = clip.snapshot(cb)
        self.assertEqual([f for f, _ in snap], [clip.CF_UNICODETEXT, HTML, 8])  # GDI handle formats dropped
        cb.data = {clip.CF_UNICODETEXT: "pasted dictation"}  # what paste() left behind
        clip.restore(snap, cb)
        self.assertEqual(cb.data, {clip.CF_UNICODETEXT: "héllo", HTML: b"<b>hi</b>", 8: b"dib-bytes"})
        self.assertEqual(cb.opened, 0)  # always closed

    def test_unreadable_format_is_skipped_not_fatal(self):
        cb = FakeCB({clip.CF_UNICODETEXT: "t", HTML: b"x"}, bad={HTML})
        self.assertEqual(clip.snapshot(cb), [(clip.CF_UNICODETEXT, "t")])
        self.assertEqual(cb.opened, 0)

    def test_hdrop_tuple_is_rebuilt_on_restore(self):
        cb = FakeCB()
        clip.restore([(clip.CF_HDROP, ("C:\\a.txt",))], cb)
        self.assertEqual(cb.data[clip.CF_HDROP], clip.build_hdrop(("C:\\a.txt",)))

    def test_over_cap_is_abandoned_not_wiped(self):  # R2: a huge image must survive a paste
        cb = FakeCB({clip.CF_UNICODETEXT: "t", 8: b"x" * 100})
        orig = clip.CAP
        clip.CAP = 50
        try:
            self.assertIsNone(clip.snapshot(cb))
            self.assertIsNone(clip.snapshot(FakeCB({8: b"x" * 100})))  # image only
        finally:
            clip.CAP = orig
        cb.data = {clip.CF_UNICODETEXT: "pasted dictation"}
        clip.restore(None, cb)
        self.assertEqual(cb.data, {clip.CF_UNICODETEXT: "pasted dictation"})  # restore skipped: our text stays

    def test_over_cap_drops_the_synthesized_dib_first(self):
        cb = FakeCB({8: b"d" * 60, 17: b"v" * 60, clip.CF_UNICODETEXT: "t"})
        orig = clip.CAP
        clip.CAP = 100
        try:
            snap = clip.snapshot(cb)
        finally:
            clip.CAP = orig
        self.assertEqual([f for f, _ in snap], [17, clip.CF_UNICODETEXT])
        png = FakeCB({8: b"d" * 60, 49500: b"p" * 60}); png.names[49500] = "PNG"
        clip.CAP = 70
        try:
            self.assertEqual([f for f, _ in clip.snapshot(png)], [49500])
        finally:
            clip.CAP = orig

    def test_failed_open_and_unsaveable_only_give_none_not_empty(self):
        stuck = FakeCB({clip.CF_UNICODETEXT: "t"}, busy=99)
        self.assertIsNone(clip.snapshot(stuck))  # busy clipboard: restore must not clear it
        self.assertIsNone(clip.snapshot(FakeCB({clip.CF_BITMAP: object()})))  # nothing round-trippable
        self.assertEqual(clip.snapshot(FakeCB()), [])  # really empty

    def test_empty_clipboard_restores_as_empty(self):
        cb = FakeCB({clip.CF_UNICODETEXT: "left by paste"})
        clip.restore([], cb)
        self.assertEqual(cb.data, {})

    def test_open_retries_then_gives_up(self):
        cb = FakeCB({clip.CF_UNICODETEXT: "t"}, busy=3)
        self.assertEqual(clip.snapshot(cb), [(clip.CF_UNICODETEXT, "t")])  # opened on the 4th try
        stuck = FakeCB({clip.CF_UNICODETEXT: "t"}, busy=99)
        self.assertIsNone(clip.snapshot(stuck))  # never raises
        clip.restore([(clip.CF_UNICODETEXT, "x")], stuck)
        self.assertEqual(stuck.data, {clip.CF_UNICODETEXT: "t"})  # untouched when it can't open


@unittest.skipUnless(os.name == "nt", "real clipboard round trip needs Windows")
class RealClipboard(unittest.TestCase):
    def test_text_and_html_round_trip_and_original_restored(self):
        import win32clipboard as cb
        original = clip.snapshot()
        try:
            html = cb.RegisterClipboardFormat("HTML Format")
            payload = b"Version:0.9\r\nStartHTML:0000000000\r\n<html><body>murmur test</body></html>"
            cb.OpenClipboard(); cb.EmptyClipboard()
            cb.SetClipboardData(clip.CF_UNICODETEXT, "murmur clip test"); cb.SetClipboardData(html, payload)
            cb.CloseClipboard()
            snap = clip.snapshot()
            cb.OpenClipboard(); cb.EmptyClipboard(); cb.SetClipboardData(clip.CF_UNICODETEXT, "clobbered"); cb.CloseClipboard()
            clip.restore(snap)
            cb.OpenClipboard()
            try:
                self.assertEqual(cb.GetClipboardData(clip.CF_UNICODETEXT), "murmur clip test")
                self.assertEqual(bytes(cb.GetClipboardData(html)).rstrip(b"\0"), payload)
            finally:
                cb.CloseClipboard()
        finally:
            clip.restore(original)  # leave the user's clipboard as it was



class EmptySelectionCopy(unittest.TestCase):  # Ethan 10-08: VS Code's UIA stand-in always reports an empty selection
    def test_vscode_marker_decides(self):
        f = clip.empty_selection_copy
        mark = lambda v: [(13, "x"), (0xC123, ('{"version":1,"isFromEmptySelection":%s}' % v).encode("utf-16-le"))]
        self.assertTrue(f(mark("true"), "whole line\n"))
        self.assertFalse(f(mark("false"), "a selected line\n"))  # a real triple-click selection of a line
        self.assertTrue(f([(0xC001, b'{"isFromEmptySelection": true}')], "x"))

    def test_without_a_marker_a_single_line_with_a_break_is_a_line_copy(self):
        f = clip.empty_selection_copy
        self.assertTrue(f([(13, "x")], "some line\n"))
        self.assertFalse(f([(13, "x")], "Eggs, bacon, cheese, sandwiches"))
        self.assertFalse(f([], "two\nlines\n"))


if __name__ == "__main__":
    unittest.main()
