import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest import mock
from murmur import brain, llm, store


class T(unittest.TestCase):
    def setUp(self):
        store.HOME = Path(tempfile.mkdtemp())
        store.ensure()

    def test_snippets(self):
        sn = {"Sig": "Best,\nEthan"}
        self.assertEqual(brain.apply_snippets("hi {{snippet:sig}}", sn), "hi Best,\nEthan")
        self.assertEqual(brain.apply_snippets("{{snippet:nope}}", sn), "{{snippet:nope}}")
        self.assertEqual(brain.snippet_only("Insert sig.", sn), "Best,\nEthan")
        self.assertIsNone(brain.snippet_only("please insert sig now", sn))

    def test_clean_shortcut_skips_llm(self):
        brain._chat = lambda *a: self.fail("llm called")
        self.assertEqual(brain.clean("insert sig", {"cfg": store.load_config()}), "Best,\nEthan")

    def test_learn_dictionary(self):
        self.assertEqual(brain.learn_dictionary("hey podstead is up", "hey Podstead is up"), ["Podstead"])
        self.assertEqual(brain.learn_dictionary("use get user", "use getUser v2"), ["getUser", "v2"])
        self.assertEqual(brain.learn_dictionary("so the thing", "so the thing"), [])
        self.assertEqual(brain.learn_dictionary("a b", "a b the cat"), [])
        self.assertEqual(brain.learn_dictionary("so the thing thanks", "So the thing. Thanks"), [])
        self.assertEqual(brain.learn_dictionary("pod stead is up", "Podstead is up"), ["Podstead"])

    def test_key_matches(self):
        self.assertTrue(store.key_matches("right ctrl", "Right Ctrl"))
        self.assertFalse(store.key_matches("ctrl", "right ctrl"))
        self.assertFalse(store.key_matches("left alt", "right alt"))
        self.assertFalse(store.key_matches(None, "right alt"))

    def test_tidy(self):
        self.assertEqual(brain.tidy('"Hi there"  \n'), "Hi there")
        self.assertEqual(brain.tidy("Here is the cleaned text:\nHi."), "Hi.")
        self.assertEqual(brain.tidy("Here: it is"), "Here: it is")
        self.assertEqual(brain.tidy("Here's the plan:\n- a\n- b"), "Here's the plan:\n- a\n- b")
        self.assertEqual(brain.tidy("'cache' is fine, use 'redis'"), "'cache' is fine, use 'redis'")
        self.assertEqual(brain.tidy("\u201cHi there\u201d"), "Hi there")

    def test_history_robust(self):
        store.append_history({"ts": "1", "final": "a"})
        store.append_history({"ts": "2", "final": "b"})
        with open(store.HOME / "history.jsonl", "a") as f:
            f.write("{torn\n")
        store.correct_last("1", "a", "A")
        self.assertEqual([h.get("corrected") for h in store.read_history()], ["A", None])

    def test_dictionary_cap_keeps_newest(self):
        store.add_terms([f"T{i}" for i in range(400)])
        self.assertIn("T399", brain.build_system_prompt({}))
        self.assertNotIn("T0,", brain.build_system_prompt({}))

    def test_prompts(self):
        store.add_terms(["Podstead", "Podstead"])
        (store.HOME / "style.md").write_text("terse", encoding="utf-8")
        store.append_history({"final": "a", "corrected": "b"})
        p = brain.build_system_prompt({"tone": "casual", "terms": ["Parakeet"]})
        for s in ("Podstead", "Parakeet", "casual", "sig", "{{snippet:", "terse", "a → b", "Output ONLY"):
            self.assertIn(s, p)
        self.assertEqual(store.load_dictionary().count("Podstead"), 1)
        c = brain.build_system_prompt({"tone": "code"}, "command")
        self.assertIn("instruction", c)
        self.assertIn("file names", c)

    def test_llm_providers(self):
        sent = []

        def fake(req, timeout=None):
            sent.append(req)
            r = mock.MagicMock(); r.__enter__.return_value = r
            r.read.return_value = b'{"choices":[{"message":{"content":"o"}}],"content":[{"type":"text","text":"a"}]}'
            return r
        base = {"model": "m", "url": "http://x"}
        with mock.patch("urllib.request.urlopen", fake), mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
            self.assertEqual(llm.chat({"llm": base}, "sys", "hi"), "o")
            self.assertEqual(llm.chat({"llm": {**base, "provider": "anthropic"}}, "sys", "hi"), "a")
        import json
        o, a = sent
        ob, ab = json.loads(o.data), json.loads(a.data)
        self.assertEqual(ob["reasoning_effort"], "none")
        self.assertNotIn("x-api-key", {k.lower() for k in o.headers})
        self.assertNotIn("reasoning_effort", ab)
        self.assertEqual((ab["system"], ab["max_tokens"]), ("sys", 2048))
        self.assertEqual(a.get_header("X-api-key"), "k")

    def test_fit(self):
        self.assertEqual(brain.fit("world", "hello"), " world")
        self.assertEqual(brain.fit(" world", "hello "), "world")
        self.assertEqual(brain.fit("World", ""), "World")
        self.assertEqual(brain.fit("World", "hi\n"), "World")
        self.assertEqual(brain.fit(".", "hello"), ".")
        self.assertEqual(brain.fit("we also fixed the login bug", "The deploy is done. "), "We also fixed the login bug")
        self.assertEqual(brain.fit("the deploy is done. we also fixed it", "The deploy is done. "), "We also fixed it")
        self.assertEqual(brain.fit("bumped", "update: we"), " bumped")
        for b, f, want in (("Hey team.", "We pushed", " We pushed"), ("update:", "we", " we"), ("hi,", "we", " we"),
                           ("Done!", "we", " We"), ("Done?", "ok", " Ok"), ('He said "', "hi", "hi"),
                           ('He said "hi"', "and", " and")):
            self.assertEqual(brain.fit(f, b), want)
        self.assertEqual(brain.fit("and", 'said "yes"'), " and")

    def test_context_section(self):
        self.assertEqual(brain._context({}), "")
        p = brain._context({"before": "We should ship", "after": "tomorrow.", "selected": "x"})
        for s in ("«We should ship»", "«tomorrow.»", "REPLACE it: «x»", "lowercase"):
            self.assertIn(s, p)
        self.assertNotIn("new sentence", p)
        done = brain._context({"before": "It is done. "})
        self.assertIn("new sentence with a capital", done)
        self.assertNotIn("lowercase", done)
        cap = brain._context({"before": "a" * 50 + "END", "cfg": {"context": {"before_chars": 3}}})
        self.assertIn("«END»", cap)
        self.assertEqual(brain._context({"before": "x", "mode": "command"}), "")
        self.assertIn("«hi»", brain.build_system_prompt({"before": "hi"}))
        self.assertNotIn("«hi»", brain.build_system_prompt({"before": "hi"}, "command"))

    def test_custom_tone_and_prompt_file(self):
        cfg = store.load_config(); cfg["tones"] = {"tweet": "Tone: tweet."}
        (store.HOME / "prompt.md").write_text("CUSTOM RULES", encoding="utf-8")
        p = brain.build_system_prompt({"cfg": cfg, "tone": "tweet"})
        self.assertTrue(p.startswith("CUSTOM RULES"))
        self.assertIn("Tone: tweet.", p)

    def test_rewrite_messages(self):
        got = []
        brain._chat = lambda cfg, sys_, user: got.append(user) or "out"
        self.assertEqual(brain.rewrite("x", "shorten", {"cfg": {}}), "out")
        brain.rewrite(None, "write hi", {"cfg": {}})
        self.assertIn("Selected text:\nx", got[0])
        self.assertIn("No selection", got[1])


if __name__ == "__main__":
    unittest.main()
