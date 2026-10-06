import sys, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_context():
    fake = {m: mock.MagicMock() for m in ("keyboard", "pyperclip", "win32api", "win32gui", "win32process")}
    with mock.patch.dict(sys.modules, fake):
        from murmur import context
    return context


class Rng:
    """Fake UIA text range over `doc` in code-point units: [s, e). `box` = {"sel": current selection, "log": selects}."""
    def __init__(self, doc, s, e, box):
        self.doc, self.s, self.e, self.box = doc, s, e, box

    def Clone(self):
        return Rng(self.doc, self.s, self.e, self.box)

    def MoveEndpointByUnit(self, endpoint, unit, count, waitTime=0):
        self.s = max(0, self.s + count)

    def Select(self, waitTime=0):
        self.box["log"].append((self.s, self.e)); self.box["sel"] = self

    def GetText(self, n):
        return self.doc[self.s:self.e]


def fake_auto(doc, caret, has_pattern=True):
    box = {"log": []}
    box["sel"] = Rng(doc, caret, caret, box)
    tp = SimpleNamespace(GetSelection=lambda: [box["sel"]])
    ctl = SimpleNamespace(GetPattern=lambda pid: tp if has_pattern else None)
    auto = SimpleNamespace(GetFocusedControl=lambda: ctl, PatternId=SimpleNamespace(TextPattern=1),
                           TextPatternRangeEndpoint=SimpleNamespace(Start=0, End=1), TextUnit=SimpleNamespace(Character=0))
    return auto, box


class SelectBeforeCaret(unittest.TestCase):
    def setUp(self):
        self.c = load_context()
        self.c._selection_reads.__defaults__ = (0.0,)  # no polling delay in tests

    def run_try(self, doc, expect, caret=None, has_pattern=True):
        auto, box = fake_auto(doc, len(doc) if caret is None else caret, has_pattern)
        state = ["unknown"]
        self.c._try_select(auto, expect, state)
        return state[0], box["log"], [box["sel"]]

    def test_ok(self):
        state, log, sel = self.run_try("hello world. I did it", " I did it")
        self.assertEqual(state, "ok")
        self.assertEqual(sel[0].GetText(-1), " I did it")

    def test_mismatch_restores_collapsed_caret(self):
        state, log, sel = self.run_try("hello world", "not there")
        self.assertEqual(state, "restored")
        self.assertEqual((sel[0].s, sel[0].e), (11, 11))  # original caret selected again

    def test_no_text_pattern_is_unknown_and_untouched(self):
        state, log, _ = self.run_try("abc", "abc", has_pattern=False)
        self.assertEqual((state, log), ("unknown", []))

    def test_utf16_miscount_retries_with_char_count(self):
        # provider counting code points: the UTF-16 count (5) over-selects, so the second try (4) must win
        state, log, sel = self.run_try("x \U0001F600 hi", "\U0001F600 hi")
        self.assertEqual(state, "ok")
        self.assertEqual(sel[0].GetText(-1), "\U0001F600 hi")

    def test_crlf_normalised(self):
        state, _, _ = self.run_try("a\nb", "a\r\nb")
        self.assertEqual(state, "ok")

    def test_abort_before_select_never_touches_the_selection(self):  # PL1
        import threading
        auto, box = fake_auto("hello world", 11)
        abort = threading.Event(); abort.set()
        state = ["unknown"]
        self.c._try_select(auto, "world", state, abort)
        self.assertEqual((state[0], box["log"]), ("restored", []))

    def test_abort_after_select_restores_the_caret(self):
        import threading
        auto, box = fake_auto("hello world", 11)
        abort = threading.Event()
        state = ["unknown"]
        real = self.c._selection_reads
        self.c._selection_reads = lambda tp, expect, wait=0.0: (abort.set(), real(tp, expect, 0.0))[1]  # caller gives up mid-select
        try:
            self.c._try_select(auto, "world", state, abort)
        finally:
            self.c._selection_reads = real
        self.assertEqual(state[0], "restored")
        self.assertEqual((box["sel"].s, box["sel"].e), (11, 11))

    def test_timeout_keeps_a_selection_the_worker_already_made(self):  # F3
        import threading
        abort, lock = threading.Event(), threading.Lock()
        self.assertEqual(self.c._resolve_timeout(["ok"], abort, lock), "ok")
        self.assertTrue(abort.is_set())
        self.assertEqual(self.c._resolve_timeout(["unknown"], threading.Event(), lock), "restored")

    def test_worker_that_loses_the_race_restores(self):
        import threading
        auto, box = fake_auto("hello world", 11)
        abort, lock = threading.Event(), threading.Lock()
        state = ["unknown"]
        real = self.c._selection_reads
        self.c._selection_reads = lambda tp, expect, wait=0.0: (abort.set(), real(tp, expect, 0.0))[1]  # caller timed out mid-verify
        try:
            self.c._try_select(auto, "world", state, abort, lock)
        finally:
            self.c._selection_reads = real
        self.assertEqual(state[0], "restored")
        self.assertEqual((box["sel"].s, box["sel"].e), (11, 11))
        self.assertEqual(self.c._resolve_timeout(state, abort, lock), "restored")

    def test_pick_rect_providers_and_utf16(self):
        self.assertEqual(self.c._utf16_len("\U0001F600a"), 3)


if __name__ == "__main__":
    unittest.main()
