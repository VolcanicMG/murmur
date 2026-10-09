import json, sys, tempfile, threading, time, unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import autolearn, brain, phonetic, store

C = brain.COMMON


class Phonetic(unittest.TestCase):
    def fix(self, raw, terms):
        return phonetic.phonetic_fix(raw, terms, C)

    def test_sound_key(self):
        self.assertEqual(phonetic.sound_key("pod stead"), phonetic.sound_key("Podstead"))
        self.assertEqual(phonetic.sound_key("get reach"), phonetic.sound_key("gitreach"))
        self.assertEqual(phonetic.sound_key("phone"), phonetic.sound_key("fone"))
        self.assertEqual(phonetic.sound_key("knight"), phonetic.sound_key("nite"))
        self.assertEqual(phonetic.sound_key("Kubernetes"), phonetic.sound_key("cooper netties"))

    def test_pod_stead(self):
        text, hard, hints = self.fix("I deployed to pod stead today.", ["Podstead"])
        self.assertEqual((text, hard), ("I deployed to Podstead today.", [("pod stead", "Podstead")]))

    def test_cooper_netties_is_a_hint_not_a_replacement(self):  # sound keys match, letter similarity is too low for a hard fix
        text, hard, hints = self.fix("we run cooper netties on it", ["Kubernetes"])
        self.assertEqual((text, hard), ("we run cooper netties on it", []))
        self.assertEqual([t for _, t in hints], ["Kubernetes"])

    def test_get_reach(self):
        self.assertEqual(self.fix("check the get reach map", ["gitreach"])[0], "check the gitreach map")

    def test_an_existing_word_equal_to_the_term_is_left_alone(self):
        self.assertEqual(self.fix("we merit a raise", ["Merit"])[0], "we merit a raise")
        self.assertEqual(self.fix("The Merit Award", ["Merit"])[0], "The Merit Award")

    def test_possessive_and_punctuation_survive(self):
        self.assertEqual(self.fix("the pod stead's logs", ["Podstead"])[0], "the Podstead's logs")
        self.assertEqual(self.fix('He said "pod stead," then left.', ["Podstead"])[0], 'He said "Podstead," then left.')
        self.assertEqual(self.fix("(pod stead)", ["Podstead"])[0], "(Podstead)")

    def test_never_crosses_sentence_punctuation(self):
        self.assertEqual(self.fix("hello. pod. stead is up", ["Podstead"])[0], "hello. pod. stead is up")
        self.assertEqual(self.fix("pod, stead", ["Podstead"])[0], "pod, stead")

    def test_longest_term_wins_on_overlap(self):
        text, hard, _ = self.fix("pod stead cloud is up", ["Podstead", "Podstead Cloud"])
        self.assertEqual(text, "Podstead Cloud is up")
        self.assertEqual([t for _, t in hard], ["Podstead Cloud"])

    def test_common_words_and_short_terms_never_fire(self):
        self.assertEqual(self.fix("write it right", ["Right"])[0], "write it right")  # the term itself is a COMMON word
        self.assertEqual(self.fix("I met john", ["Jon"])[0], "I met john")  # under 4 letters
        self.assertEqual(self.fix("a gift each", ["gitreach"])[0], "a gift each")
        self.assertEqual(self.fix("the new tool", ["Podstead", "Kubernetes"])[0], "the new tool")

    def test_close_single_words_are_hints_never_replacements(self):  # R4
        for raw, term in (("my depgard works", "depguard"), ("tong is fine", "Toung")):
            text, hard, hints = self.fix(raw, [term])
            self.assertEqual((text, hard), (raw, []), raw)
            self.assertEqual([t for _, t in hints], [term], raw)

    def test_wrongful_replacements_are_gone(self):  # R4: the six review probes
        for raw, term in (("the cloud is down", "Claude"), ("my tongue hurts", "Toung"), ("toning down the volume", "Toung"),
                          ("open get the reach tool", "gitreach"), ("the deaf guard is ok", "depguard"), ("the depot guard is ok", "depguard")):
            text, hard, _ = self.fix(raw, [term])
            self.assertEqual((text, hard), (raw, []), raw)

    def test_split_names_still_become_hard_replacements(self):
        self.assertEqual(self.fix("deploy pod stead now", ["Podstead"])[0], "deploy Podstead now")
        self.assertEqual(self.fix("run Kuber nets now", ["Kubernetes"])[0], "run Kubernetes now")
        self.assertEqual(self.fix("use dep guard", ["depguard"])[0], "use depguard")
        self.assertEqual(self.fix("the get reach map", ["gitreach"])[0], "the gitreach map")
        self.assertEqual(self.fix("a pod stead and a pod", ["Podstead"])[0], "a Podstead and a pod")  # words around it stay

    def test_hints_reach_the_prompt(self):
        ctx = {"hints": [("cooper netties", "Kubernetes")] * 2 + [(f"h{i}", f"T{i}") for i in range(9)]}
        out = brain._hints(ctx)
        self.assertIn("'cooper netties' may be 'Kubernetes'", out)
        self.assertEqual(out.count("may be"), 5)  # capped
        self.assertEqual(brain._hints({}), "")


class PrivateLogs(unittest.TestCase):  # R7
    def test_rejected_cleanup_logs_only_sizes(self):
        brain._chat = lambda *a: "Totally unrelated invented content about elephants and tractors."
        for private, leaks in ((True, False), (False, False)):  # the log is never encrypted: sizes only, private or not
            ctx = {"cfg": {"llm": {}}, "tone": "neutral", "private": private}
            with self.assertLogs("murmur", "WARNING") as cm:
                brain.clean_checked("my secret passphrase is hunter two", ctx, retry=False)
            joined = " ".join(cm.output)
            self.assertEqual("hunter" in joined, leaks, private)
            self.assertIn("coverage=", joined)


class DetectEdit(unittest.TestCase):
    P = "Let's talk to john tomorrow about the deploy."

    def test_name_fix_learns_the_name(self):
        edited = brain.detect_edit(self.P, "Hi. Let's talk to Jon tomorrow about the deploy.")
        self.assertEqual(edited, "Let's talk to Jon tomorrow about the deploy.")
        self.assertEqual(brain.learn_from_edit(self.P, edited), (["Jon"], True))

    def test_case_only_fix_learns_nothing_and_is_not_recorded(self):
        edited = brain.detect_edit(self.P, "Let's talk to John tomorrow about the deploy.")
        self.assertIsNotNone(edited)
        self.assertEqual(brain.learn_from_edit("talk to john", "talk to John"), ([], False))

    def test_only_spelling_and_hearing_fixes_count_as_corrections(self):  # R6
        e = brain.edit_is_a_correction
        self.assertTrue(e("talk to john tomorrow", "talk to Jon tomorrow"))
        self.assertTrue(e("we run cooper netties now", "we run Kubernetes now"))  # sounds alike (different spelling)
        self.assertFalse(e("deploy to staging tonight", "deploy to production tonight"))  # changed his mind
        self.assertFalse(e("let's meet on tuesday at noon", "let's meet on friday at noon"))
        self.assertFalse(e("I like the blue one", "I like the one"))  # a deletion
        self.assertFalse(e("send it to sara today", "send it to sara today please"))  # an addition
        self.assertFalse(e("The release is on Monday morning.", "The release is on Tuesday evening."))
        self.assertEqual(brain.learn_from_edit("deploy to staging tonight", "deploy to production tonight"), ([], False))
        # near-identical text typed elsewhere with different content: found by detect_edit, rejected here
        pasted = "We shipped the build to staging yesterday."
        field = "We shipped the build to production yesterday."
        edited = brain.detect_edit(pasted, field)
        self.assertEqual(brain.learn_from_edit(pasted, edited)[1], False)

    def test_staging_to_production_never_marks_the_row(self):
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        p = "Deploy to staging tonight."
        store.append_history({"ts": "2026-10-06T10:00:00", "mode": "dictate", "final": p, "raw": p})
        e = {"app": "code.exe", "title": "t", "text": p, "ts": "2026-10-06T10:00:00", "final": p, "t": 0}
        self.assertEqual(autolearn.apply([(e, "Deploy to production tonight.")], {}), [])
        self.assertNotIn("corrected", store.read_history()[-1])
        # a deleted-and-retyped paste with different content: same
        e2 = {**e, "text": "Please call Sara about the invoice."}
        autolearn.apply([(e2, "Please email Sara about the refund.")], {})
        self.assertNotIn("corrected", store.read_history()[-1])

    def test_unchanged_gone_and_rewritten(self):
        self.assertIsNone(brain.detect_edit(self.P, self.P))
        self.assertIsNone(brain.detect_edit(self.P, "something else entirely"))
        self.assertIsNone(brain.detect_edit(self.P, "We need to completely rethink the release plan for next week."))
        self.assertIsNone(brain.detect_edit(self.P, "Let's talk to Jon about a totally different thing, deploy later maybe tomorrow"))  # > 4 words changed

    def test_more_typing_after_the_text_still_matches(self):
        field = "Let's talk to Jon tomorrow about the deploy. And then I wanted to add one more thing"
        self.assertEqual(brain.detect_edit(self.P, field), "Let's talk to Jon tomorrow about the deploy.")

    def test_short_text_and_deleted_words(self):
        self.assertEqual(brain.detect_edit("talk to john", "talk to Jon"), "talk to Jon")
        self.assertIsNone(brain.detect_edit(self.P, "Let's talk to tomorrow about the deploy."))  # a deletion is not a correction

    def test_boundaries_are_word_boundaries(self):
        field = "xx Let's talk to Jon tomorrow about the deploy. yy"
        self.assertEqual(brain.detect_edit(self.P, field), "Let's talk to Jon tomorrow about the deploy.")

    def test_two_pastes_in_a_row(self):
        a, b = "We shipped the build to prod.", "Then I told sara about the outage."
        pend = []
        pend += [{"app": "code.exe", "title": "t", "text": a, "ts": "1", "final": a, "t": 1000.0},
                 {"app": "code.exe", "title": "t", "text": b, "ts": "2", "final": b, "t": 1010.0}]
        field = "We shipped the build to prod. Then I told Sara about the outage."
        hits = autolearn.check("code.exe", field, now=1100.0, pending=pend)
        self.assertEqual([(e["ts"], x) for e, x in hits], [("2", "Then I told Sara about the outage.")])
        self.assertEqual([e["ts"] for e in pend], ["1"])  # the unchanged one stays pending
        self.assertEqual(autolearn.check("code.exe", field, now=1000 + 31 * 60, pending=pend), [])
        self.assertEqual(pend, [])  # expired after 30 min
        other = [{"app": "slack.exe", "title": "t", "text": b, "ts": "3", "final": b, "t": 1000.0}]
        self.assertEqual(autolearn.check("code.exe", field, now=1100.0, pending=other), [])  # a different app is never read

    def test_apply_adds_terms_and_marks_the_row(self):
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        store.append_history({"ts": "2026-10-06T10:00:00", "mode": "dictate", "final": self.P, "raw": self.P})
        e = {"app": "code.exe", "title": "t", "text": self.P, "ts": "2026-10-06T10:00:00", "final": self.P, "t": 0}
        learned = autolearn.apply([(e, "Let's talk to Jon tomorrow about the deploy.")], {})
        self.assertEqual(learned, ["Jon"])
        self.assertIn("Jon", store.load_dictionary())
        self.assertEqual(store.read_history()[-1]["corrected"], "Let's talk to Jon tomorrow about the deploy.")

    def test_remember_keeps_five_stripped_pastes(self):
        autolearn._pending.clear()
        for i in range(7):
            autolearn.remember(autolearn.Paste("code.exe", "t", f" text {i} ", str(i), "final"))
        self.assertEqual([e["ts"] for e in autolearn._pending], ["2", "3", "4", "5", "6"])
        self.assertEqual((autolearn._pending[0]["text"], autolearn._pending[0]["app"]), ("text 2", "code.exe"))
        self.assertGreater(autolearn._pending[0]["t"], 0)
        autolearn.remember(autolearn.Paste("a", "t", "x", "9", "f", 123.0))
        self.assertEqual(autolearn._pending[-1]["t"], 123.0)
        autolearn._pending.clear()

    def test_private_mode_and_switch_off_learn_nothing(self):
        autolearn._pending.clear()
        autolearn._pending.append({"app": "code.exe", "title": "t", "text": self.P, "ts": "1", "final": self.P, "t": time.time()})
        field = "Let's talk to Jon tomorrow about the deploy."
        self.assertEqual(autolearn.check_and_apply("code.exe", field, {"privacy": {"private": True}}), [])
        self.assertEqual(autolearn.check_and_apply("code.exe", field, {"learning": {"auto_edits": False}}), [])
        self.assertEqual(len(autolearn._pending), 1)  # still pending: nothing consumed it
        autolearn._pending.clear()

    def test_watch_never_reads_a_terminal_or_private(self):  # R10
        reads = []
        rd = lambda c, a=None: reads.append(c) or ("windowsterminal.exe", "x")
        e = {"app": "windowsterminal.exe", "text": "x", "ts": "1", "final": "x", "t": 0}
        self.assertIsNone(autolearn.watch({"apps": {"no_copy": ["windowsterminal.exe"]}}, e, lambda: False, rd, lambda: "", kb=FakeKb()))
        self.assertIsNone(autolearn.watch({"privacy": {"private": True}}, {**e, "app": "code.exe"}, lambda: False, rd, lambda: "", kb=FakeKb()))
        self.assertIsNone(autolearn.watch({"learning": {"auto_edits": False}}, {**e, "app": "code.exe"}, lambda: False, rd, lambda: "", kb=FakeKb()))
        self.assertEqual(reads, [])


class FakeKb:
    """keyboard stand-in: records the hook callback and every unhook."""
    def __init__(self):
        self.cb, self.unhooked = None, []

    def hook(self, cb):
        self.cb = cb
        return "h"

    def unhook(self, h):
        self.unhooked.append(h)

    def is_modifier(self, name):
        return name in ("ctrl", "right ctrl", "shift", "alt")

    def press(self, name):
        self.cb(SimpleNamespace(event_type="down", name=name))


FAST = {"BASELINE_S": 0, "POLL_S": 0.01, "SETTLE_S": 0.05, "WATCH_S": 0.6, "HANDOFF_S": 0.5}


class Watch(unittest.TestCase):
    P = "Store the keys in bolts today."

    def setUp(self):
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        store.append_history({"ts": "t1", "mode": "dictate", "final": self.P, "raw": self.P})
        autolearn._pending.clear()
        self.patches = [mock.patch.object(autolearn, k, v) for k, v in FAST.items()]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        autolearn._pending.clear()

    def run_watch(self, texts, script, busy=lambda: False, app=lambda: "code.exe", cfg=None):
        kb, reads, self.expects = FakeKb(), list(texts), []
        e = autolearn.remember(autolearn.Paste("code.exe", "t", self.P, "t1", self.P))
        rd = lambda c, a=None: self.expects.append(a) or ("code.exe", reads.pop(0) if len(reads) > 1 else reads[0])
        t = autolearn.watch({} if cfg is None else cfg, e, busy, rd, app, kb=kb)
        while kb.cb is None:
            time.sleep(0.005)
        script(kb)
        t.join(3)
        self.assertEqual(kb.unhooked, ["h"])  # the hook always goes, by handle
        return store.read_history()[-1]

    def test_no_keys_is_unchanged(self):
        row = self.run_watch(["Hi. " + self.P], lambda kb: None)
        self.assertEqual((row["outcome"], row["outcome_source"], row["edits"]), ("unchanged", "watch", []))
        self.assertNotIn("corrected", row)

    def test_a_settled_edit_then_enter_is_recorded_and_learned(self):
        def script(kb):
            kb.press("v"); time.sleep(0.2); kb.press("enter")
        row = self.run_watch(["Hi. " + self.P, "Hi. Store the keys in vaults today."], script)
        self.assertEqual(row["outcome"], "edited")
        self.assertEqual(row["edits"], [{"kind": "misheard", "from": "bolts", "to": "vaults"}])
        self.assertEqual(row["sent"], "Store the keys in vaults today.")
        self.assertEqual(row["corrected"], "Store the keys in vaults today.")
        self.assertEqual(autolearn._pending, [])

    def test_keys_without_a_settled_read_stay_unknown(self):
        def script(kb):
            kb.press("v"); kb.press("enter")
        with self.assertLogs("murmur", "INFO") as cm:
            row = self.run_watch(["Hi. " + self.P, "changed"], script)
        self.assertNotIn("outcome", row)
        self.assertIn("unknown", " ".join(cm.output))
        self.assertEqual(len(autolearn._pending), 1)  # left for hook (a)
        self.assertNotIn("watching", autolearn._pending[0])

    def test_modifiers_alone_are_not_keys(self):
        row = self.run_watch(["Hi. " + self.P], lambda kb: kb.press("right ctrl"))
        self.assertEqual(row["outcome"], "unchanged")

    def test_a_new_session_hands_its_read_to_the_watch(self):
        state = {"busy": False}
        def script(kb):
            time.sleep(0.05); state["busy"] = True; time.sleep(0.05); state["busy"] = False  # the paste's own session ends
            kb.press("x"); state["busy"] = True  # typed, then dictated again at once
            autolearn.check_and_apply("code.exe", "Hi. " + self.P + " And more.", {})
        row = self.run_watch(["Hi. " + self.P], script, busy=lambda: state["busy"])
        self.assertEqual((row["outcome"], row["added"]), ("updated", 2))

    def test_private_mid_watch_drops_everything(self):  # cfg is live: the tray's private toggle
        cfg = {}
        def script(kb):
            kb.press("v"); time.sleep(0.2); cfg["privacy"] = {"private": True}
        row = self.run_watch(["Hi. " + self.P, "Hi. Store the keys in vaults today."], script, cfg=cfg)
        self.assertNotIn("outcome", row)
        self.assertNotIn("corrected", row)
        self.assertEqual(autolearn._pending, [])  # not left for hook (a) either
        self.assertEqual(set(self.expects), {"code.exe"})  # every read names the paste's app

    def test_learning_switched_off_before_the_record_records_nothing(self):
        cfg = {}
        def script(kb):
            kb.press("v"); time.sleep(0.2); cfg["learning"] = {"auto_edits": False}; kb.press("enter")
        row = self.run_watch(["Hi. " + self.P, "Hi. Store the keys in vaults today."], script, cfg=cfg)
        self.assertNotIn("outcome", row)

    def test_a_handoff_from_another_field_is_unknown(self):
        state = {"busy": False}
        def script(kb):
            time.sleep(0.05); state["busy"] = True; time.sleep(0.05); state["busy"] = False
            kb.press("x"); state["busy"] = True
            autolearn.check_and_apply("code.exe", "totally different field the fix main", {})
        row = self.run_watch(["Hi. " + self.P], script, busy=lambda: state["busy"])
        self.assertNotIn("outcome", row)

    def test_poll_returns_reason_and_newest_read(self):
        e, w = {"app": "code.exe"}, {"stop": threading.Event()}
        keys = {"last": time.monotonic() + 0.02, "enter": False}  # typed just after the poll began
        ticks = iter([False] * 20 + [True] * 100)
        reason, newest, read_t = autolearn._poll({}, e, w, keys, lambda: False, lambda c, a: ("code.exe", "text"),
                                                 lambda: "slack.exe" if next(ticks) else "code.exe")
        self.assertEqual((reason, newest), ("app", "text"))
        self.assertGreaterEqual(read_t, keys["last"])
        self.assertEqual(autolearn._poll({"privacy": {"private": True}}, e, w, keys, lambda: False, None, None), ("private", None, None))

    def test_app_switch_ends_the_watch(self):
        row = self.run_watch(["Hi. " + self.P], lambda kb: kb.press("x"), app=lambda: "slack.exe")
        self.assertNotIn("outcome", row)  # typed, never read again: unknown


class WatchLogic(unittest.TestCase):
    P = "Store the keys in bolts today."

    def test_should_read_after_a_pause_only(self):
        self.assertFalse(autolearn.should_read(10.0, None, 0.0))  # no keys
        self.assertFalse(autolearn.should_read(10.0, 9.8, 0.0))  # still typing
        self.assertTrue(autolearn.should_read(10.0, 9.0, 0.0))
        self.assertFalse(autolearn.should_read(10.0, 9.0, 9.5))  # already read after that key

    def test_finish_reasons(self):
        f = autolearn.finish_reason
        self.assertIsNone(f(1, False, False, False, False))
        self.assertEqual(f(1, True, False, False, False), "enter")
        self.assertEqual(f(1, False, True, False, False), "replaced")
        self.assertEqual(f(1, False, False, True, False), "session")
        self.assertEqual(f(1, False, False, False, True), "app")
        self.assertEqual(f(autolearn.WATCH_S, False, False, False, False), "timeout")

    def test_judge_regions(self):
        base = "Hello there. " + self.P + " Bye."
        self.assertEqual(autolearn.judge(self.P, base, base)["outcome"], "unchanged")
        after = autolearn.judge(self.P, base, base + " And one more thing.")
        self.assertEqual((after["outcome"], after["added"]), ("updated", 4))
        before = autolearn.judge(self.P, base, "Oh. " + base)
        self.assertEqual(before["outcome"], "updated")
        old_text = autolearn.judge(self.P, base, base.replace("Hello", "Howdy"))  # an edit to text that was already there
        self.assertEqual(old_text["outcome"], "updated")
        inside = autolearn.judge(self.P, base, base.replace("bolts", "vaults"))
        self.assertEqual((inside["outcome"], inside["edits"][0]["kind"], inside["added"]), ("edited", "misheard", 0))
        tail = autolearn.judge(self.P, base, base.replace("today. Bye.", "today, I think."))  # overlap reaching past the paste
        self.assertEqual((tail["outcome"], tail["added"]), ("updated", 2))  # "Bye." was there before, "I think" typed
        self.assertIsNone(autolearn.judge(self.P, "something else", "something else!"))  # paste not found: unknown
        self.assertIsNone(autolearn.judge(self.P, "Hi. " + self.P, "totally different field the fix main"))  # another field
        self.assertEqual(autolearn.judge(self.P, self.P, "Keep the keys in bolts today.")["outcome"], "edited")  # last 3 words anchor
        self.assertIsNone(autolearn.judge(self.P, self.P, "Store the vaults now"))  # neither end survives
        self.assertEqual(autolearn.judge(self.P, base.replace(" ", "\r\n", 1), base.replace(" ", "\r\n", 1))["outcome"], "unchanged")

    def test_watch_result(self):
        self.assertEqual(autolearn.watch_result(self.P, self.P, None, False, False)["outcome"], "unchanged")
        self.assertIsNone(autolearn.watch_result(self.P, self.P, None, True, False))
        self.assertIsNone(autolearn.watch_result(self.P, self.P, self.P + " x", True, False))
        self.assertEqual(autolearn.watch_result(self.P, self.P, self.P + " x", True, True)["outcome"], "updated")


class ClassifyEdit(unittest.TestCase):  # Ethan's real shapes
    c = staticmethod(brain.classify_edit)

    def test_restored_word(self):
        r = self.c("Honestly we'd be better at shipping it.", "Honestly no, we'd be better at shipping it.")
        self.assertEqual((r["outcome"], r["edits"]), ("edited", [{"kind": "restored", "from": "", "to": "no,"}]))

    def test_added_second_sentence_is_updated(self):
        r = self.c("Put it in the repo.", "Put it in the repo. Then deploy it tomorrow.")
        self.assertEqual((r["outcome"], r["added"], r["edits"], r["span"]), ("updated", 4, [], "Put it in the repo."))
        r = self.c("Put it in the repo.", "Quick note: put it in the repo")
        self.assertEqual((r["outcome"], r["added"]), ("updated", 2))

    def test_misheard_and_word_choice(self):
        r = self.c("Store the keys in bolts today.", "Store the keys in vaults today.")
        self.assertEqual(r["edits"], [{"kind": "misheard", "from": "bolts", "to": "vaults"}])
        r = self.c("Clean up the old items now.", "Clean up the old repos now.")
        self.assertEqual(r["edits"], [{"kind": "word_choice", "from": "items", "to": "repos"}])
        r = self.c("I deployed pot stead today.", "I deployed Podstead today.")
        self.assertEqual(r["edits"][0]["kind"], "misheard")  # merged words: stricter MERGE_SIM
        r = self.c("bolts are here", "So vaults are here")  # an edge word replaced while text was typed before it
        self.assertEqual((r["added"], r["edits"][0]["to"], r["span"]), (1, "vaults", "vaults are here"))

    def test_removed(self):
        r = self.c("Okay, ship it now please.", "ship it now please.")
        self.assertEqual((r["outcome"], r["edits"]), ("edited", [{"kind": "removed", "from": "Okay,", "to": ""}]))

    def test_case_and_punctuation_are_not_edits(self):
        self.assertEqual(self.c("okay ship it now please", "Okay. Ship it NOW, please!")["outcome"], "unchanged")

    def test_rewrite_and_missing(self):
        r = self.c("Okay ship it now please", "Please hold the release until the review is done")
        self.assertEqual((r["outcome"], r["rewrite"], r["edits"]), ("edited", True, []))
        self.assertEqual(self.c("Hi there", "nothing alike")["outcome"], "missing")

    def test_caps(self):
        r = self.c(" ".join(f"w{i}" for i in range(40)), " ".join(f"w{i}" if i % 2 else f"x{i}" * 60 for i in range(40)))
        self.assertTrue(len(r["edits"]) <= 8 and all(len(e["to"]) <= 200 for e in r["edits"]))

    def test_learning_rule(self):
        p = "Let's talk to john tomorrow about the deploy."
        r = self.c(p, "Let's talk to Jon tomorrow about the deploy, ok?")
        self.assertEqual(brain.learn_from_result(p, r), (["Jon"], "Let's talk to Jon tomorrow about the deploy,"))
        r = self.c("Deploy it to staging tonight.", "Deploy it to Production tonight.")  # changed mind: data only
        self.assertEqual(brain.learn_from_result("Deploy it to staging tonight.", r), ([], None))
        r = self.c("Ask about the release.", "Ask Pete about the release.")  # restored: never a term
        self.assertEqual(brain.learn_from_result("Ask about the release.", r), ([], None))


class OutcomeFields(unittest.TestCase):
    def test_record_writes_fields_and_only_edited_rows_get_sent(self):
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        for ts in ("a", "b"):
            store.append_history({"ts": ts, "mode": "dictate", "final": "Clean up the old items now.", "raw": "x"})
        e = {"ts": "a", "final": "Clean up the old items now.", "text": "Clean up the old items now."}
        autolearn.record(e, brain.classify_edit(e["text"], "Clean up the old repos now."), "claude-code")
        autolearn.record({**e, "ts": "b"}, brain.classify_edit(e["text"], e["text"] + " Thanks."), "watch")
        a, b = store.read_history()
        self.assertEqual((a["outcome"], a["outcome_source"], a["sent"], a["rewrite"], a["added"]),
                         ("edited", "claude-code", "Clean up the old repos now.", False, 0))
        self.assertNotIn("corrected", a)  # word choice: data only
        self.assertEqual((b["outcome"], b["added"]), ("updated", 1))
        self.assertNotIn("sent", b)


class StyleRefresh(unittest.TestCase):
    def setUp(self):
        store.HOME = Path(tempfile.mkdtemp()); store.ensure()
        import threading; autolearn._style_lock = threading.Lock()

    def rows(self, n):
        for i in range(n):
            store.append_history({"ts": f"2026-10-0{i % 9 + 1}T10:00:00", "mode": "dictate", "final": f"message number {i}", "raw": "x"})

    def test_due_needs_the_count_30_rows_and_the_switch(self):
        cfg = {"learning": {"style_every": 3}}
        self.assertFalse(autolearn.style_due(cfg, 40))  # no dictations counted yet
        for _ in range(3):
            autolearn.count_dictation()
        self.assertTrue(autolearn.style_due(cfg, 40))
        self.assertFalse(autolearn.style_due(cfg, 29))  # under 30 dictate rows
        self.assertFalse(autolearn.style_due({"learning": {"style_every": 3, "style_auto": False}}, 40))
        self.assertFalse(autolearn.style_due({"learning": {"style_every": 3}, "privacy": {"private": True}}, 40))

    def test_refresh_writes_style_and_resets_the_counter(self):
        self.rows(35)
        for _ in range(3):
            autolearn.count_dictation()
        cfg = {"learning": {"style_every": 3}}
        with mock.patch.object(brain, "learn_style", return_value="Short, friendly messages."):
            t = autolearn.maybe_refresh_style(cfg, lambda: False)
            t.join(5)
        self.assertEqual(store.load_style(), "Short, friendly messages.")
        self.assertEqual(store.load_state()["since_style"], 0)

    def test_deferred_while_busy_and_failure_keeps_the_old_style(self):
        self.rows(35)
        (store.HOME / "style.md").write_text("old style\n", encoding="utf-8")
        for _ in range(3):
            autolearn.count_dictation()
        cfg = {"learning": {"style_every": 3}}
        self.assertIsNone(autolearn.maybe_refresh_style(cfg, lambda: True))  # a session is live
        self.assertEqual(store.load_state()["since_style"], 3)  # still due
        with mock.patch.object(brain, "learn_style", side_effect=RuntimeError("llm down")):
            autolearn.maybe_refresh_style(cfg, lambda: False).join(5)
        self.assertEqual(store.load_style(), "old style")
        self.assertEqual(store.load_state()["since_style"], 0)  # retry after another style_every

    def test_state_file_is_atomic_json(self):
        autolearn.count_dictation()
        self.assertEqual(store.load_state(), {"since_style": 1})
        self.assertFalse((store.HOME / "state.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()


class AcronymsAndPlurals(unittest.TestCase):  # review 10-07
    T = ["LM Studio", "Podstead", "AI Studio", "JSON API", "OBS Studio"]

    def fix(self, s):
        from murmur import phonetic, brain
        return phonetic.phonetic_fix(s, self.T, brain.COMMON)[0]

    def test_spoken_acronym_is_fixed(self):
        self.assertEqual(self.fix("open Elm Studios now"), "open LM Studio now")
        self.assertEqual(self.fix("Elm studio is open"), "LM Studio is open")

    def test_real_plurals_and_present_terms_are_left_alone(self):
        for s in ("I run two pod steads now", "LM Studios are great", "AI studios", "see the obs studios", "the jason apis",
                  "the film studios were busy", "Helm Studio charts"):
            self.assertEqual(self.fix(s), s, s)


class PluralHint(unittest.TestCase):
    def test_plural_sound_alike_hints_the_plural_term(self):  # Ethan 10-08: "two pod sets" became "two Podstead"
        text, hard, hints = phonetic.phonetic_fix("We run two pod sets now.", ["Podstead"])
        self.assertEqual((text, hard), ("We run two pod sets now.", []))
        self.assertIn(("pod sets", "Podsteads"), hints)
        self.assertEqual(phonetic.phonetic_fix("in Elm Studios", ["LM Studio"])[0], "in LM Studio")  # acronym -s: still hard


class ShortNameHints(unittest.TestCase):
    def test_short_names_need_a_close_match(self):  # "Pullet" was hinted as Pete and the LLM swapped every list cue
        hints = lambda raw: [t for _, t in phonetic.phonetic_fix(raw, ["Pete"])[2]]
        self.assertEqual(hints("Pullet one, fix it"), [])
        self.assertEqual(hints("ask Peat about it"), ["Pete"])  # a real near miss still hints


class Round3(unittest.TestCase):  # Ethan 10-08 test round
    TERMS = ["Pete", "Podstead", "LM Studio", "Postgres"]

    def test_a_dictionary_word_is_never_remapped(self):  # "Pete and I" pasted as "Podstead and I"
        self.assertEqual(phonetic.phonetic_fix("Pete and I tested it in LM Studio.", self.TERMS)[1:], ([], []))

    def test_a_mishearing_keeps_its_first_sound(self):
        self.assertEqual(phonetic.phonetic_fix("we tested it", ["Podstead"])[2], [])
        self.assertIn(("Posteads", "Podsteads"), phonetic.phonetic_fix("Both Posteads are down.", self.TERMS)[2])  # heard plural

    def test_sentence_start_words_are_not_learned_as_names(self):  # the fix panel learned "Ask" and "Eggs"
        self.assertEqual(brain.learn_dictionary("Speed about the release.", "Ask Pete about the release."), ["Pete"])
        self.assertEqual(brain.learn_dictionary("We had eggs, bacon", "Eggs, bacon"), [])
        self.assertEqual(brain.learn_dictionary("pod stead is up", "Podstead is up"), ["Podstead"])  # a sound-alike fix still teaches

    def test_a_recapitalised_word_after_a_symbol_is_not_a_name(self):  # Ethan 10-08: "Throw" learned from a terminal screen
        pasted = "Can you throw out a detailed plan so tomorrow we can start on this?"
        self.assertEqual(brain.learn_from_edit(pasted, "\U0001f3af Throw out a detailed plan so tomorrow we can start on this?"), ([], False))
        self.assertEqual(brain.learn_from_edit("I deployed pot stead today.", "I deployed Podstead today."), (["Podstead"], True))

    def test_guard_counts_offered_hints_as_applied(self):  # "The pot set is up." -> "Podstead is up." was pasted raw
        self.assertFalse(brain.plausible("The pot set is up.", "Podstead is up."))
        judged = brain.with_hints("The pot set is up.", [("pot set", "Podstead")], "Podstead is up.")
        self.assertTrue(brain.plausible(judged, "Podstead is up."))
        unused = brain.with_hints("check out the feature login", [("feature", "Podstead")], "Check out the feature login.")
        self.assertEqual(unused, "check out the feature login")  # a hint the model ignored changes nothing

    def test_term_case_restored_after_cleanup(self):
        self.assertEqual(brain.restore_term_case("Both podsteads are down, pvp too. murmur", ["Podstead", "PvP", "murmur"]),
                         "Both Podsteads are down, PvP too. murmur")
        self.assertEqual(brain.restore_term_case("Murmur is up.", ["murmur"]), "Murmur is up.")  # lowercase terms untouched


class ReplayNameMisses(unittest.TestCase):
    def test_missed_names(self):
        from evals import run
        self.assertEqual(run.names("I'm on GitHub. Ask Dr. Nguyen about LM Studio."), ["GitHub", "Nguyen", "LM", "Studio"])
        self.assertEqual(run.missed_names("Ask Dr. Nguyen about LM Studio.", "Ask Dr. Win about Lm Studio."), ["Nguyen", "LM"])


class ClaudeCode(unittest.TestCase):  # evals.build --claude-code
    def setUp(self):
        from evals import build
        self.b = build

    def line(self, ts, content, **kw):
        return json.dumps({"type": "user", "isSidechain": False, "timestamp": ts, "message": {"role": "user", "content": content}, **kw})

    @staticmethod
    def utc(local, **delta):
        """A transcript timestamp ("...Z") for a local naive history ts shifted by `delta`."""
        t = datetime.fromisoformat(local).astimezone(timezone.utc) + timedelta(**delta)
        return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def test_human_text_shapes(self):
        h = self.b.human_text
        self.assertEqual(h(json.loads(self.line("x", "hi there"))), "hi there")
        self.assertEqual(h(json.loads(self.line("x", [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]))), "a\nb")
        self.assertIsNone(h(json.loads(self.line("x", [{"type": "tool_result", "content": "out"}]))))
        self.assertIsNone(h(json.loads(self.line("x", "sub", isSidechain=True))))
        self.assertIsNone(h(json.loads(self.line("x", "caveat", isMeta=True))))
        self.assertIsNone(h({"type": "assistant", "message": {"content": "hi"}}))

    def test_match_window_and_timezones(self):
        ts, final = "2026-10-08T14:00:00", "Please check the vaults config."
        row = {"ts": ts, "final": final}
        at = lambda **d: (datetime.fromisoformat(self.utc(ts, **d).replace("Z", "+00:00")), "Please check the vaults config. Thanks")
        self.assertEqual(self.b.match_row(row, [at(seconds=40)])["outcome"], "updated")
        self.assertEqual(self.b.match_row(row, [at(seconds=-3)])["outcome"], "updated")
        self.assertIsNone(self.b.match_row(row, [at(seconds=-6)]))
        self.assertIsNone(self.b.match_row(row, [at(minutes=11)]))
        other = (at(seconds=10)[0], "Something unrelated entirely")
        self.assertIsNone(self.b.match_row(row, [other, at(seconds=20)]))  # only the first message is a candidate

    def test_podcast_is_recorded_but_teaches_nothing(self):  # reviewer: "Podstead" -> "podcast" poisoned `corrected`
        ts, final = "2026-10-08T14:00:00", "Check the Podstead logs."
        msg = (datetime.fromisoformat(self.utc(ts, seconds=20).replace("Z", "+00:00")), "check the podcast logs for errors please")
        res = self.b.match_row({"ts": ts, "final": final}, [msg])
        self.assertEqual(res["outcome"], "edited")
        self.assertFalse(self.b.strong(res))  # 3 of 4 words matched
        home = Path(tempfile.mkdtemp()); saved = store.HOME
        try:
            store.HOME = home; store.ensure()
            store.append_history({"ts": ts, "mode": "dictate", "final": final, "raw": "r"})
            autolearn.record({"ts": ts, "final": final, "text": final}, res, "claude-code", learn=self.b.strong(res))
            row = store.read_history()[-1]
            self.assertEqual((row["outcome"], row["sent"]), ("edited", "check the podcast logs"))
            self.assertNotIn("corrected", row)
            self.assertNotIn("podcast", store.load_dictionary())
        finally:
            store.HOME = saved
        strong_one = brain.classify_edit("Store the keys in bolts today.", "Store the keys in vaults today. Thanks")
        self.assertTrue(self.b.strong(strong_one))
        self.assertFalse(self.b.strong(brain.classify_edit("a b c d e", "a b c d x y z q r s t u e")))  # span too long

    def test_writes_outcomes_for_terminal_rows_only(self):
        home, cc = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "proj"
        cc.mkdir()
        rows = [{"ts": "2026-10-08T14:00:00", "mode": "dictate", "app": "windowsterminal.exe", "final": "Store the keys in bolts today.", "raw": "r"},
                {"ts": "2026-10-08T14:05:00", "mode": "dictate", "app": "windowsterminal.exe", "final": "Never sent anywhere at all.", "raw": "r"},
                {"ts": "2026-10-08T14:06:00", "mode": "dictate", "app": "code.exe", "final": "Store the keys in bolts today.", "raw": "r"}]
        (home / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        (cc / "s.jsonl").write_text("\n".join([
            self.line(self.utc(rows[0]["ts"], seconds=30), [{"type": "text", "text": "Store the keys in vaults today. And the other one."}]),
            self.line(self.utc(rows[2]["ts"], seconds=5), "Store the keys in vaults today."), "{torn"]), encoding="utf-8")
        saved = store.HOME
        try:
            counts = self.b.claude_code(home / "history.jsonl", [cc.parent])
            a, b, c = store.read_history()
        finally:
            store.HOME = saved
        self.assertEqual(counts, {"edited": 1, "unmatched": 1})
        self.assertEqual((a["outcome"], a["outcome_source"], a["added"], a["edits"][0]["kind"]), ("edited", "claude-code", 4, "misheard"))
        self.assertEqual(a["corrected"], a["sent"])
        self.assertNotIn("outcome", b)
        self.assertNotIn("outcome", c)  # a readable app: the live watch's job

    def test_history_case_expects_what_was_sent(self):
        r = {"raw": "r", "final": "Store the keys in bolts today.", "outcome": "edited", "sent": "Store the keys in vaults today.",
             "edits": [{"kind": "misheard", "from": "bolts", "to": "vaults"}]}
        case = self.b.history_case(1, r)
        self.assertEqual(case["expected"], "Store the keys in vaults today.")
        self.assertIn("EDITED", case["note"]); self.assertIn("misheard", case["note"])
        self.assertEqual(self.b.history_case(1, {**r, "rewrite": True})["expected"], r["final"])
