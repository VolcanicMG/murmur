import sys, tempfile, time, unittest
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

    def test_delayed_check_skips_when_busy_or_app_changed(self):
        calls = []
        with mock.patch.object(autolearn, "check_and_apply", side_effect=lambda *a: calls.append(a)):
            autolearn.schedule({}, "code.exe", lambda: True, lambda c: ("code.exe", "x"), delay=0).join()
            autolearn.schedule({}, "code.exe", lambda: False, lambda c: ("slack.exe", "x"), delay=0).join()
            self.assertEqual(calls, [])
            autolearn.schedule({}, "code.exe", lambda: False, lambda c: ("code.exe", "text"), delay=0).join()
        self.assertEqual(len(calls), 1)


    def test_delayed_check_never_reads_a_terminal(self):  # R10
        reads = []
        cfg = {"apps": {"no_copy": ["windowsterminal.exe"]}}
        autolearn.schedule(cfg, "windowsterminal.exe", lambda: False, lambda c: reads.append(c) or ("windowsterminal.exe", "x"), delay=0).join()
        self.assertEqual(reads, [])
        autolearn.schedule(cfg, "code.exe", lambda: False, lambda c: reads.append(c) or ("code.exe", ""), delay=0).join()
        self.assertEqual(len(reads), 1)


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
        self.assertIn(("Posteads", "Podstead"), phonetic.phonetic_fix("Both Posteads are down.", self.TERMS)[2])

    def test_sentence_start_words_are_not_learned_as_names(self):  # the fix panel learned "Ask" and "Eggs"
        self.assertEqual(brain.learn_dictionary("Speed about the release.", "Ask Pete about the release."), ["Pete"])
        self.assertEqual(brain.learn_dictionary("We had eggs, bacon", "Eggs, bacon"), [])
        self.assertEqual(brain.learn_dictionary("pod stead is up", "Podstead is up"), ["Podstead"])  # a sound-alike fix still teaches

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
