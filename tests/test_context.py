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


class Password(unittest.TestCase):
    def setUp(self):
        self.c = load_context()

    def fake(self, is_password):
        def boom(*a, **k):
            raise AssertionError("a password field must never be read")
        ctl = SimpleNamespace(IsPassword=is_password, GetPattern=boom, BoundingRectangle=SimpleNamespace(left=0, top=0, right=300, bottom=30),
                              Name="Password", ClassName="Edit", AutomationId="pw")
        return SimpleNamespace(GetFocusedControl=lambda: ctl, PatternId=SimpleNamespace(TextPattern=1, ValuePattern=2, TextPattern2=3))

    def test_password_field_returns_flag_and_reads_nothing(self):  # A1
        got = []
        out = self.c._read(self.fake(True), self.c.ReadOpts({}, lambda r, k: got.append((r, k)), True))
        self.assertEqual(out, {"password": True})
        self.assertEqual(got, [((0, 0, 300, 30), "field")])  # card geometry only

    def test_verdict_is_published_before_any_text_read(self):  # R3
        order = []
        v = self.c.Verdict()
        orig = v.publish
        v.publish = lambda x: (order.append(("published", x)), orig(x))[1]
        got = []
        auto = self.fake(True)
        self.c._read(auto, self.c.ReadOpts({}, None, True, 0, v))
        self.assertEqual((v.value, order), (True, [("published", True)]))
        self.assertTrue(v.wait(0.01))
        slow = self.c.Verdict()
        self.assertIsNone(slow.wait(0.01))  # a reader that never answers leaves it unknown
        slow.publish(None); slow.publish(True)  # first answer wins
        self.assertIsNone(slow.value)

    def test_read_failure_publishes_unknown(self):
        v = self.c.Verdict()
        with mock.patch.dict(sys.modules, {"uiautomation": None}):  # import fails inside read_field
            self.assertEqual(self.c.read_field({}, on_rect=lambda r, k: None, verdict=v), {})
        self.assertTrue(v._event.is_set() and v.value is None)

    def test_is_password_failure_reads_as_not_password(self):
        self.assertFalse(self.c._is_password(SimpleNamespace()))  # no IsPassword attribute
        self.assertTrue(self.c._is_password(SimpleNamespace(IsPassword=True)))


class FakeCtl:
    def __init__(self, name="", kind="TextControl", top=0, bottom=0, kids=(), parent=None):
        self.Name, self.ControlTypeName, self._kids, self._parent = name, kind, list(kids), parent
        self.BoundingRectangle = SimpleNamespace(top=top, bottom=bottom, left=0, right=100)
        for k in self._kids:
            k._parent = self

    def GetChildren(self):
        return self._kids

    def GetParentControl(self):
        return self._parent


class Thread(unittest.TestCase):  # B4
    def setUp(self):
        self.c = load_context()

    def tree(self, n=5):
        msgs = [FakeCtl(f"message {i}", "ListItemControl", top=i * 20, bottom=i * 20 + 18) for i in range(n)]
        below = FakeCtl("sidebar below the input", "TextControl", top=500, bottom=520)
        box = FakeCtl("", "EditControl", top=400, bottom=440)
        root = FakeCtl("", "PaneControl", kids=[FakeCtl("", "PaneControl", kids=msgs), below, box])
        return root, box

    def test_ordering_and_cap_keep_the_bottom_most_in_reading_order(self):
        nodes = [(0, 18, "oldest message"), (20, 38, "middle message"), (40, 58, "newest message"), (500, 520, "below the input")]
        self.assertEqual(self.c.select_thread(nodes, 400, 1000), "oldest message middle message newest message")
        self.assertEqual(self.c.select_thread(nodes, 400, 20), "newest message")  # bottom-most first until the cap
        self.assertEqual(self.c.select_thread([(0, 18, "x" * 100)], 400, 10), "x" * 10)  # one long message keeps its tail
        self.assertEqual(self.c.select_thread([(0, 18, "same"), (20, 38, "same")], 400, 100), "same")  # de-duplicated
        self.assertEqual(self.c.select_thread([(390, 410, "overlaps the input")], 400, 100), "")  # not entirely above

    def test_collects_from_the_window_not_from_below_the_input(self):
        root, box = self.tree()
        got = self.c.thread_text(box, 1000)
        self.assertEqual(got, "message 0 message 1 message 2 message 3 message 4")
        self.assertNotIn("sidebar", got)

    def test_time_budget_cuts_the_walk_off(self):
        root, box = self.tree(50)
        ticks = iter([0.0] + [0.01 * i for i in range(1, 100)])
        nodes = self.c.collect_thread(box, now=lambda: next(ticks))
        self.assertLess(len(nodes), 50)  # stopped at 150 ms, not after the whole tree
        self.assertTrue(nodes)

    def test_node_cap(self):
        root, box = self.tree(300)
        self.assertLessEqual(len(self.c.collect_thread(box)), self.c.THREAD_NODES)

    def test_remote_provider_refuses(self):
        c = self.c
        local = {"context": {"thread": True}, "llm": {"provider": "openai", "url": "http://localhost:1234/v1/chat/completions"}}
        self.assertEqual(c.thread_cap(local, True), 600)
        self.assertEqual(c.thread_cap({**local, "llm": {"provider": "anthropic"}}, True), 0)
        self.assertEqual(c.thread_cap({**local, "llm": {**local["llm"], "fallback": True}, "llm_fallback": {"provider": "anthropic"}}, True), 0)
        self.assertEqual(c.thread_cap({**local, "llm": {**local["llm"], "fallback": True}, "llm_fallback": {"provider": "openai", "url": "http://127.0.0.1:1/v1"}}, True), 600)
        self.assertEqual(c.thread_cap({**local, "llm": {"provider": "builtin"}}, True), 600)
        self.assertEqual(c.thread_cap({"context": {"thread": False}, "llm": {"provider": "builtin"}}, True), 0)  # off by default

    def test_no_copy_apps_and_password_fields_refuse(self):
        local = {"context": {"thread": True}, "llm": {"provider": "builtin"}}
        self.assertEqual(self.c.thread_cap(local, False), 0)  # text=False is what a no_copy app (terminal) passes
        root, box = self.tree()
        box.IsPassword = True
        auto = SimpleNamespace(GetFocusedControl=lambda: box)
        self.assertEqual(self.c._read(auto, self.c.ReadOpts({}, None, True, 600)), {"password": True})  # returns before any thread walk

    def test_prompt_section(self):
        from murmur import brain
        self.assertIn("reference only", brain._thread({"thread": "hello there"}))
        self.assertEqual(brain._thread({}), "")
        self.assertEqual(brain._thread({"thread": "x", "mode": "command"}), "")


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



class Placeholder(unittest.TestCase):
    def test_vscode_stand_in_selection_is_unknown(self):  # probe 10-08: sel_known=True, 0 chars, while text was selected
        from murmur import context
        name = "The editor is not accessible at this time. To enable screen reader optimized mode, use Shift+Alt+F1"
        self.assertTrue(context.placeholder(name, ("", "\n", "", True)))
        self.assertTrue(context.placeholder("Message", ("", "", "", True)))  # nothing readable: can't vouch for "no selection"
        self.assertFalse(context.placeholder("Text Editor", ("Hello there", "", "", True)))


class ReadText(unittest.TestCase):  # the paste watch's read
    def test_terminals_and_other_apps_are_never_read(self):
        context = load_context()
        reads, cfg = [], {"apps": {"no_copy": ["windowsterminal.exe"]}}
        field = lambda *a, **k: reads.append(1) or {"before": "ab", "selected": "", "after": "c"}
        for fg, expect, want in (("code.exe", None, ("code.exe", "abc")), ("code.exe", "code.exe", ("code.exe", "abc")),
                                 ("slack.exe", "code.exe", ("slack.exe", "")), ("windowsterminal.exe", None, ("windowsterminal.exe", ""))):
            reads.clear()
            with mock.patch.object(context.win, "active_app", return_value=(fg, "t")), mock.patch.object(context, "read_field", field):
                self.assertEqual(context.read_text(cfg, expect), want, fg)
            self.assertEqual(len(reads), 1 if want[1] else 0, fg)


class WakeAnchor(unittest.TestCase):  # Ethan 10-09: card sat on Discord's window fallback until the next dictation
    def test_a_caret_found_on_the_second_ask_moves_the_card(self):
        c = load_context()
        got = []
        auto = SimpleNamespace(GetFocusedControl=lambda: object())
        with mock.patch.object(c, "WAKE_S", 0), mock.patch.object(c, "_control_rect", lambda ctl: (0, 0, 900, 700)), \
             mock.patch.object(c, "_selection_range", lambda a, ctl: None), \
             mock.patch.object(c, "_line_rect", lambda a, sel: (380, 640, 900, 660)), \
             mock.patch.object(c, "_caret_providers", lambda a, ctl, sel: []):
            c._wake_anchor(auto, lambda r, k: got.append((r, k)), (0, 0, 900, 700))
        self.assertEqual(got, [((0, 640, 900, 660), "caret")])

    def test_still_no_caret_leaves_the_fallback_alone(self):
        c = load_context()
        got = []
        with mock.patch.object(c, "WAKE_S", 0), mock.patch.object(c, "_control_rect", lambda ctl: None), \
             mock.patch.object(c, "_selection_range", lambda a, ctl: None), mock.patch.object(c, "_line_rect", lambda a, sel: None), \
             mock.patch.object(c, "_caret_providers", lambda a, ctl, sel: []):
            c._wake_anchor(SimpleNamespace(GetFocusedControl=lambda: object()), lambda r, k: got.append((r, k)), (0, 0, 900, 700))
        self.assertEqual(got, [])

if __name__ == "__main__":
    unittest.main()
