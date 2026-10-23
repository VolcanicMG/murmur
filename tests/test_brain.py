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

    def test_spoken_command(self):
        for ok in ("scratch that", "Scratch that.", "Um, scratch that!", "undo", "delete that", "uh undo that"):
            self.assertEqual(brain.spoken_command(ok), "undo", ok)
        for no in ("scratch that please", "please undo", "new paragraph", "scratch", ""):
            self.assertIsNone(brain.spoken_command(no), no)

    def test_redictation(self):
        self.assertEqual(brain.redictation_terms("talk to john", "talk to Jon"), ["Jon"])
        self.assertEqual(brain.redictation_terms("talk to john", "send the invoice tomorrow"), [])
        self.assertEqual(brain.redictation_terms("we should ship this today", "They could deploy Podstead tomorrow"), [])
        self.assertEqual(brain.redictation_terms("Thanks, sounds good.", "Thanks, sounds good!"), [])
        self.assertEqual(brain.redictation_terms("same words here", "same words here"), [])
        self.assertTrue(brain.is_redictation("talk to john", "talk to Jon"))
        self.assertFalse(brain.is_redictation("talk to john", "talk to john and then Mary"))

    def test_style_cannot_override_rules(self):
        (store.HOME / "style.md").write_text("uses ellipses...", encoding="utf-8")
        p = brain.build_system_prompt({"tone": "neutral"})
        self.assertIn("uses ellipses...\nStyle never overrides the rules above: self-corrections are applied, fillers removed.", p)
        self.assertTrue(p.endswith("Output ONLY the final text."))
        self.assertIn("self-corrections", p.splitlines()[-1])

    def test_numbers_and_clean_word(self):
        self.assertEqual(brain.learn_dictionary("is 5 ok", "is 6 ok"), [])
        self.assertEqual(brain.learn_dictionary("at 1.5 ok", "at 2,5 or 10:30 or 3/4 ok"), [])
        self.assertEqual(brain.clean_word('  "Podstead,"! '), "Podstead")
        self.assertEqual(brain.clean_word("(foo.bar)"), "foo.bar")

    def test_no_copy_default(self):
        self.assertIn("windowsterminal.exe", store.no_copy({"apps": {}}))
        self.assertIn("wt.exe", store.no_copy({}))
        self.assertEqual(store.no_copy({"apps": {"no_copy": ["x.exe"]}}), ["x.exe"])

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
        self.assertEqual(brain.fit("world", "well hello to the"), " world")
        self.assertEqual(brain.fit(" world", "well hello to the "), "world")
        self.assertEqual(brain.fit("World", ""), "World")
        self.assertEqual(brain.fit("World", "hi\n"), "World")
        self.assertEqual(brain.fit(".", "hello"), ".")
        self.assertEqual(brain.fit("we also fixed the login bug", "The deploy is done. "), "We also fixed the login bug")
        self.assertEqual(brain.fit("the deploy is done. we also fixed it", "The deploy is done. "), "We also fixed it")
        self.assertEqual(brain.fit("bumped", "the update we all"), " bumped")
        for b, f, want in (("Hey team.", "We pushed", " We pushed"), ("update:", "we", " we"), ("hi,", "we", " we"),
                           ("Done!", "we", " We"), ("Done?", "ok", " Ok"), ('He said "', "hi", "hi"),
                           ('He said "hi"', "and", " and")):
            self.assertEqual(brain.fit(f, b), want)
        self.assertEqual(brain.fit("and", 'said "yes"'), " and")

    def test_context_section(self):
        self.assertEqual(brain._context({}), "")
        p = brain._context({"before": "We should ship it and", "after": "tomorrow.", "selected": "x"})
        for s in ("«We should ship it and»", "«tomorrow.»", "REPLACE it: «x»", "lowercase"):
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

    def test_old_default_prompt_copy_gets_current_default(self):
        import hashlib
        old = "OLD SHIPPED RULES"
        store.OLD_PROMPTS.add(hashlib.sha256(old.encode()).hexdigest()[:16])
        self.addCleanup(store.OLD_PROMPTS.discard, hashlib.sha256(old.encode()).hexdigest()[:16])
        (store.HOME / "prompt.md").write_text(old + "\n", encoding="utf-8")
        self.assertEqual(store.load_prompt(), store.DEFAULT_PROMPT.strip())
        store.save_prompt(store.DEFAULT_PROMPT)  # "Reset to default" leaves no frozen copy behind
        self.assertFalse((store.HOME / "prompt.md").exists())
        store.save_prompt("MINE")
        self.assertEqual(store.load_prompt(), "MINE")

    def test_rewrite_messages(self):
        got = []
        brain._chat = lambda cfg, sys_, user: got.append(user) or "out"
        self.assertEqual(brain.rewrite("x", "shorten", {"cfg": {}}), "out")
        brain.rewrite(None, "write hi", {"cfg": {}})
        self.assertIn("Selected text:\nx", got[0])
        self.assertIn("No selection", got[1])

    # ---- command, scratch and capitalisation fixes (real history rows)
    def test_apply_scratch(self):
        a = brain.apply_scratch
        self.assertEqual(a("Um testing testing one two three um scratch that"), "")
        self.assertEqual(a("Testing, testing, one, two, three. Uh hello, hello, uh scratch that. We need to continue focusing on the main focal point."),
                         "Testing, testing, one, two, three. We need to continue focusing on the main focal point.")
        self.assertEqual(a("Uh scratch that"), "")
        self.assertEqual(a("A. B, scratch that. C."), "A. C.")
        self.assertEqual(a("Scratch that. C."), "C.")
        self.assertEqual(a("A. B, scratch that. C, scratch that. D."), "A. D.")
        self.assertEqual(a("A. B. Um. scratch that last one. C."), "A. C.")
        self.assertEqual(a("Tuesday works, scratch that, let's do Thursday."), "let's do Thursday.")
        self.assertEqual(a("nothing to see"), "nothing to see")
        self.assertTrue(brain.has_scratch("Delete that.") and not brain.has_scratch("scratching"))

    def test_cues_in_ordinary_sentences_do_not_fire(self):
        for t in ("I'll delete that file later.", "Don't scratch that itch.", "Never mind that noise, we ship today.",
                  "Can you strike that clause from the contract?", "Did you delete that?", "I said never mind that."):
            self.assertFalse(brain.has_scratch(t), t)
            self.assertEqual(brain.apply_scratch(t), t)
        self.assertEqual(brain.apply_scratch("Hello. Um delete that. Bye."), "Bye.")  # clause-opening cue still works
        self.assertEqual(brain.apply_scratch("Hello. Never mind that, we go."), "we go.")

    def test_scratch_does_not_treat_inner_dots_as_sentence_ends(self):  # D1
        a = brain.apply_scratch
        self.assertEqual(a("Deploy 0.44 now. Scratch that."), "")
        self.assertEqual(a("See example.com today. Scratch that."), "")
        self.assertEqual(a("Ship it. Deploy 0.44 now. Scratch that."), "Ship it.")
        self.assertEqual(a("Version 0.44 is out, scratch that, 0.45 is out."), "0.45 is out.")
        self.assertEqual(a("The 2.0 release ships. Never mind that."), "")
        self.assertEqual(a("Mail a.b@example.com now. Hello there. Scratch that."), "Mail a.b@example.com now.")

    def test_spoken_parentheses_become_brackets(self):  # Ethan 10-22: "(" came out as the word "parentheses"
        f = brain.spoken_forms
        self.assertEqual(f("The tool open paren beta close paren ships Friday."), "The tool (beta) ships Friday.")
        self.assertEqual(f("It returns a list, open parenthesis, sorted, close parenthesis."), "It returns a list (sorted).")
        self.assertEqual(f("Put it in parentheses please."), "Put it in parentheses please.")  # ordinary speech
        r = brain.restore_parens
        self.assertEqual(r("The tool (beta) ships.", "The tool beta ships."), "The tool (beta) ships.")
        self.assertEqual(r("The tool (beta) ships.", "The tool parentheses beta ships."), "The tool (beta) ships.")
        self.assertEqual(r("The tool (beta) ships.", "The tool (beta) ships."), "The tool (beta) ships.")

    def test_scratch_that_replaces_only_the_clause_a_short_correction_repeats(self):  # Ethan 10-08: got "Other repos."
        a = brain.apply_scratch
        self.assertEqual(a("Same format as the vaults, other items. Scratch that. Other repos."), "Same format as the vaults, other repos.")
        self.assertEqual(a("Call me tomorrow, okay. Scratch that. Text me."), "Text me.")  # no shared first word: whole sentence
        self.assertEqual(a("Let's meet Tuesday. Scratch that. Let's meet Wednesday."), "Let's meet Wednesday.")

    def test_scratch_that_after_a_frame_word_is_ordinary_speech(self):  # D1b
        for t in ("Don't scratch that.", "I told you to scratch that.", "Please do not scratch that", "We should just scratch that"):
            self.assertFalse(brain.has_scratch(t), t)
            self.assertEqual(brain.apply_scratch(t), t)
        self.assertEqual(brain.apply_scratch("Um testing testing one two three um scratch that"), "")  # Ethan's row
        self.assertFalse(brain.has_scratch("Tuesday works scratch that, let's do Thursday."))  # mid-transcript needs punctuation

    def test_chunks_never_use_the_snippet_shortcut(self):  # PL2
        brain._chat = lambda *a: "cleaned"
        ctx = {"cfg": store.load_config(), "tone": "neutral"}
        self.assertEqual(brain.clean_checked("insert sig", ctx), ("Best,\nEthan", True))
        text, _ = brain.clean_checked("insert sig", ctx, shortcut=False)
        self.assertNotEqual(text, "Best,\nEthan")

    def test_command_intent(self):
        ci = brain.command_intent
        self.assertEqual(ci("Uh scratch that"), ("undo", None))
        self.assertEqual(ci("Scratch that last one."), ("undo", None))
        self.assertEqual(ci("Um scratch that last one. Um instead of doing a we need X."), ("scratch", "instead of doing a we need X."))
        self.assertEqual(ci("Scratch that. Um let's do anything I would change currently."), ("scratch", "let's do anything I would change currently."))
        self.assertEqual(ci("Add this word to the dictionary."), ("learn", None))
        self.assertEqual(ci("save that term in my dictionary"), ("learn", None))
        self.assertEqual(ci("learn this word"), ("learn", None))
        self.assertEqual(ci("Add Toung to the dictionary."), ("learn", "Toung"))
        self.assertEqual(ci("add the cat to the dictionary")[0], "rewrite")
        self.assertEqual(ci("make it formal"), ("rewrite", "make it formal"))
        self.assertEqual(ci("delete that paragraph"), ("rewrite", "delete that paragraph"))
        self.assertEqual(ci("strike that word"), ("rewrite", "strike that word"))
        self.assertEqual(ci("Scratch that last one. Instead we go."), ("scratch", "Instead we go."))
        self.assertEqual(ci("Delete that."), ("undo", None))
        # R7: dictionary intents only where unambiguous
        self.assertEqual(ci("add this word to the dictionary"), ("learn", None))
        self.assertEqual(ci("add it to the dictionary")[0], "rewrite")  # no noun: needs "my dictionary"
        self.assertEqual(ci("add it to my dictionary"), ("learn", None))
        self.assertEqual(ci("add it to murmur's dictionary"), ("learn", None))
        self.assertEqual(ci("add bananas to the dictionary", has_selection=True)[0], "rewrite")  # a selected dict literal
        self.assertEqual(ci("add these to my dictionary")[0], "rewrite")
        self.assertEqual(ci("add bananas to the dictionary", tone="code")[0], "rewrite")
        self.assertEqual(ci("add bananas to my dictionary", tone="code"), ("learn", "bananas"))

    def test_resolve_target(self):
        rt, now = brain.resolve_target, 1000.0
        lp = (now - 30, "code.exe", "I think this is working. ")
        before = "x\r\nI think this is working.\r\n"
        self.assertEqual(rt((True, "pick"), before, lp, ("code.exe", now)), ("selection", "pick"))
        self.assertEqual(rt((True, ""), before, lp, ("code.exe", now)), ("last", "I think this is working."))
        self.assertEqual(rt((True, ""), before, lp, ("slack.exe", now)), ("new", ""))  # other app
        self.assertEqual(rt((True, ""), before, (now - 601, "code.exe", lp[2]), ("code.exe", now)), ("new", ""))  # too old
        self.assertEqual(rt((True, ""), "I think this is working. And more", lp, ("code.exe", now)), ("new", ""))  # moved on
        self.assertEqual(rt((True, ""), "", lp, ("code.exe", now)), ("new", ""))  # before unavailable: no guess
        self.assertEqual(rt((False, ""), before, lp, ("code.exe", now)), ("new", ""))  # no TextPattern: no guess
        self.assertEqual(rt((True, ""), before, (0.0, "", ""), ("code.exe", now)), ("new", ""))
        self.assertEqual(rt((False, "clip"), "", lp, ("code.exe", now)), ("selection", "clip"))  # clipboard fallback result

    def test_rewrite_last_prompt(self):
        seen = []
        brain._chat = lambda cfg, system, user: seen.append(user) or "fixed"
        ctx = {"cfg": store.load_config(), "tone": "code"}
        self.assertEqual(brain.rewrite("Hello there.", "change there to world", ctx, "last"), "fixed")
        self.assertIn("what the user just dictated", seen[0])
        self.assertIn("Hello there.", seen[0])
        self.assertIn("change there to world", seen[0])
        brain.rewrite("Hello there.", "make it formal", ctx)
        self.assertTrue(seen[1].startswith("Selected text:"))

    def test_continuation_and_fit(self):
        c = brain.continuation
        self.assertEqual(c(""), "new")
        self.assertEqual(c("Message @someone"), "new")
        self.assertEqual(c("We shipped it and"), "mid")
        self.assertEqual(c("const x = foo"), "new")
        self.assertEqual(c("this is a code_thing here and some_var"), "new")
        self.assertEqual(c("Hello,"), "mid")
        self.assertEqual(c("Done.\n"), "new")
        self.assertEqual(c("Done."), "new")
        self.assertEqual(c('She said "We are done."'), "new")  # closing quote after a finished sentence
        self.assertEqual(c("(see the note)"), "new")
        self.assertEqual(c('She said "'), "mid")
        self.assertEqual(c("say \u201chello"), "new")  # curly quote is not in the opener set
        self.assertEqual(brain.fit("what's your schedule?", ""), "What's your schedule?")
        self.assertEqual(brain.fit("what's your schedule?", "Message @someone"), " What's your schedule?")
        self.assertEqual(brain.fit("and then we left", "We shipped it and"), " and then we left")  # mid keeps the LLM's casing
        self.assertEqual(brain.fit("echo hello", "", "code"), "echo hello")  # terminal: before unknown in code tone
        self.assertEqual(brain.fit("vim app.py", "", "code"), "vim app.py")
        self.assertEqual(brain.fit("testing testing one two three", "", "casual"), "Testing testing one two three")
        self.assertEqual(brain.fit("we left", 'She said "We are done."', "code"), ' We left')  # known before still capitalises
        self.assertEqual(brain.fit("- item", ""), "- Item")
        self.assertEqual(brain.fit("1. item", ""), "1. Item")
        self.assertEqual(brain.fit("my_var is set", ""), "my_var is set")
        self.assertEqual(brain.fit("--force now", ""), "--force now")
        self.assertEqual(brain.fit("app.py is it", ""), "app.py is it")
        self.assertEqual(brain.tidy_raw("hello there"), "Hello there.")

    def test_number_self_correction_keeps_only_the_final_value(self):  # E1
        a = brain.apply_scratch
        self.assertEqual(a("bump the version to 0.43, actually 0.44, and push"), "bump the version to 0.44, and push")
        self.assertEqual(a("bump it to zero point forty three actually zero point forty four and push"), "bump it to zero point forty four and push")
        self.assertEqual(a("meet at three no wait four o'clock"), "meet at four o'clock")
        self.assertEqual(a("call five I mean six people"), "call six people")
        self.assertEqual(a("we leave on Tuesday no wait Thursday"), "we leave on Tuesday no wait Thursday")  # words are the LLM's job
        self.assertEqual(a("it is actually fine"), "it is actually fine")
        self.assertEqual(a("version 1.2 actually 1.3 and then 2.0 actually 2.1"), "version 1.3 and then 2.1")

    def test_spoken_quotes(self):
        q = brain.spoken_quotes
        self.assertEqual(q("a grey quote Starting end quote card"), 'a grey "Starting" card')
        self.assertEqual(q("he said open quote we are done close quote. Then left"), 'he said "we are done". Then left')
        self.assertEqual(q("quote hello, unquote, and more"), '"hello", and more')
        self.assertEqual(q("I will quote you on that"), "I will quote you on that")  # no closing cue: left alone
        self.assertEqual(q("the thing was quote super quote cool"), 'the thing was "super" cool')  # bare second quote closes
        self.assertEqual(brain.spoken_forms("say quote forty two end quote now"), 'say "forty two" now')  # runs inside spoken_forms

    def test_live_view(self):
        lv = brain.live_view
        self.assertEqual(lv("um hello there. how are you", "um hello there", "Hello there."), ("Hello there. how are", "you"))
        self.assertEqual(lv("Hello there, how are you", "hello there.", "Hello there."), ("Hello there. how are", "you"))  # re-punctuated
        self.assertEqual(lv("hello there", "hello there", "Hello there."), ("Hello there.", ""))  # fully covered
        self.assertEqual(lv("goodbye now", "hello there", "Hello there."), ("goodbye", "now"))  # stale: raw
        self.assertEqual(lv("", "", ""), ("", ""))
        lst = "* Fix the card flicker.\n* Test the hotkeys."  # Ethan 10-08: raw words trailed the last bullet
        self.assertEqual(lv("Bullet one, fix the card flicker. Bullet two, test the hotkeys. Bullet three, ship",
                            "Bullet one, fix the card flicker. Bullet two, test the hotkeys.", lst),
                         (lst + "\nBullet three,", "ship"))
        self.assertEqual(lv("a. b c", "a.", "* A."), ("* A.\nb", "c"))  # one finished item, no line break yet
        self.assertEqual(lv("a. test the hot", "a. test", "* A.\n* Test"), ("* A.\n* Test the", "hot"))  # unfinished item continues
        self.assertEqual(lv("Hello there. how are you", "hello there.", "Hello there."), ("Hello there. how are", "you"))  # prose: same line

    def test_digits_before_units(self):  # replay suite 10-07: "fifteen hundred milliseconds" stayed words
        d = brain.digits_before_units
        self.assertEqual(d("Set the timeout to fifteen hundred milliseconds and retry count to three."),
                         "Set the timeout to 1500 milliseconds and retry count to three.")
        self.assertEqual(d("wait three seconds"), "wait three seconds")  # small number, prose unit: words stay
        self.assertEqual(d("it took twenty five minutes"), "it took 25 minutes")
        self.assertEqual(d("five percent of users"), "5 percent of users")  # technical unit: always digits
        self.assertEqual(d("one or two seconds"), "one or two seconds")  # not one number: untouched
        self.assertEqual(brain.apply_scratch("set it to fifteen hundred milliseconds"), "set it to 1500 milliseconds")
        # review 10-07: compound numbers and number tails
        self.assertEqual(d("we got one thousand five hundred requests"), "we got 1500 requests")  # was 100500
        self.assertEqual(d("two thousand three hundred milliseconds"), "2300 milliseconds")
        self.assertEqual(d("two hundred and fifty milliseconds"), "250 milliseconds")
        for keep in ("zero point twenty five percent", "eleven point fifteen seconds", "Ten years ago we met", "it is ten times better",
                     "We met. Twelve years later"):
            self.assertEqual(d(keep), keep, keep)
        from murmur import prepass
        self.assertEqual(prepass._number("one thousand five hundred".split(), 0), ("1500", 4))
        self.assertEqual(prepass._number("two hundred and five".split(), 0), ("205", 4))
        self.assertEqual(prepass._number("two hundred and then".split(), 0), ("200", 2))  # 'and' only joins numbers

    def test_list_cues(self):  # Ethan 10-08: "bullet" heard as Pullet/Pulit, then swapped for "Pete"
        lc = brain.apply_scratch
        self.assertEqual(lc("Pullet one. Fix the card flicker. Pulit two. Fix this. Pulit three."),
                         "Bullet one. Fix the card flicker. Bullet two. Fix this. Bullet three.")
        self.assertEqual(lc("pull it 2, ship it"), "bullet 2, ship it")
        self.assertEqual(lc("Pull it over here."), "Pull it over here.")  # no number after it: untouched
        self.assertTrue(brain.plausible("Bullet one. Fix the card flicker. Bullet two. Fix this. Bullet three.",
                                        "- Fix the card flicker\n- Fix this"))  # the cue words are not dropped content

    def test_even_list_case(self):  # Ethan 10-08: "* Eggs\n* bacon\n* cheese"
        e = brain.even_list_case
        self.assertEqual(e("* Eggs\n* bacon\n* cheese"), "* Eggs\n* Bacon\n* Cheese")
        self.assertEqual(e("1. Eggs\n2. bacon"), "1. Eggs\n2. Bacon")
        self.assertEqual(e("- eggs\n- bacon"), "- eggs\n- bacon")  # consistently lowercase: left alone
        self.assertEqual(e("Eggs and bacon"), "Eggs and bacon")

    def test_quote_said_quote_end_quote(self):  # Ethan 10-08 replay: the word "quote" before the real opener
        q = brain.spoken_quotes
        self.assertEqual(q("Quote said, quote, Ship it Friday, end quote. So let's plan."), 'Quote said, "Ship it Friday". So let\'s plan.')
        self.assertEqual(q("the quote was quote fine unquote"), 'the quote was "fine"')
        self.assertEqual(q("it was quote super quote cool"), 'it was "super" cool')  # bare closer still works

    def test_restore_quotes(self):  # Ethan 10-06: the LLM stripped the marks spoken_quotes put in
        rq, p = brain.restore_quotes, brain.apply_scratch
        self.assertEqual(rq(p("Quote, super, quote, cool."), "super cool."), '"super" cool.')
        self.assertEqual(rq(p("Quote, super, end quote, cool."), "super, cool."), '"super", cool.')
        self.assertEqual(rq(p("She asked, quote, Are we shipping Thursday? End quote. And nobody answered."),
                            "She asked, Are we shipping Thursday? And nobody answered."),
                         'She asked, "Are we shipping Thursday?" And nobody answered.')
        self.assertEqual(rq('"hello" there', '"hello" there'), '"hello" there')  # already quoted: untouched
        self.assertEqual(rq('"hello" there', "hi there"), "hi there")  # the model rephrased it: never invent
        self.assertEqual(p("she asked quote are we done? end quote. ok"), 'she asked "are we done?" ok')  # no '?".'

    def test_restore_breaks(self):
        rb = brain.restore_breaks
        self.assertEqual(rb("plums,\nand a bag of oranges.", "Groceries: pineapples, pears, peaches, plums, and a bag of oranges."),
                         "Groceries: pineapples, pears, peaches, plums,\nand a bag of oranges.")
        self.assertEqual(rb("First point done.\n\nSecond point.", "First point done. Second point."), "First point done.\n\nSecond point.")
        kept = "First point done.\n\nSecond point."
        self.assertEqual(rb("First point done.\n\nSecond point.", kept), kept)  # already there
        self.assertEqual(rb("a b\nc", "totally different"), "totally different")  # no anchor: untouched

    def test_spoken_breaks(self):  # E2
        a = brain.apply_scratch
        self.assertEqual(a("first point done new paragraph second point done"), "first point done\n\nsecond point done")
        self.assertEqual(a("First point done. New paragraph. Second point done."), "First point done.\n\nSecond point done.")
        self.assertEqual(a("one thing new line another thing"), "one thing\nanother thing")
        for keep in ("I wrote a new paragraph of text", "add a new line here", "the new line of code", "start a new paragraph"):
            self.assertEqual(a(keep), keep, keep)
        self.assertEqual(a("new paragraph"), "new paragraph")  # nothing around it: left for the LLM

    def test_number_words_collapse_for_the_guard(self):  # E3
        w = brain._words
        self.assertEqual(w("back in nineteen ninety nine"), w("Back in 1999."))
        self.assertEqual(w("in twenty twenty six"), ["in", "2026"])
        self.assertEqual(w("call five five five one two three four"), w("Call 555-1234."))
        self.assertEqual(w("five five five one two three four five six seven"), w("(555) 123-4567"))
        self.assertEqual(w("one two three"), ["1", "2", "3"])  # a short spoken count stays separate
        self.assertEqual(w("zero point forty four"), ["0.44"])
        self.assertEqual(w("it costs twelve dollars and fifty cents"), w("It costs $12.50."))
        self.assertTrue(brain.plausible("it costs twelve dollars and fifty cents", "It costs $12.50."))
        self.assertEqual(w("twelve dollars"), ["12", "dollars"])  # no cents: unchanged
        self.assertTrue(brain.plausible("back in nineteen ninety nine", "Back in 1999."))
        self.assertTrue(brain.plausible("call me at five five five one two three four", "Call me at 555-1234."))

    def test_spoken_addresses(self):  # E6
        a = brain.apply_scratch
        self.assertEqual(a("send it to john dot smith at example dot com"), "send it to john.smith@example.com")
        self.assertEqual(a("visit double u double u double u dot example dot com slash docs"), "visit www.example.com/docs")
        self.assertEqual(a("see example dot org slash a slash b dot html now"), "see example.org/a/b.html now")
        self.assertEqual(a("meet me at example dot com"), "meet me at example.com")  # 'me' is not an address
        for keep in ("the dot com bubble", "we met at the cafe", "a dot product of two vectors", "look at example dot foo"):
            self.assertEqual(a(keep), keep, keep)

    def test_code_tone_keeps_noun_phrases_as_prose(self):  # E5
        r = brain.restore_prose_identifiers
        code = {"tone": "code"}
        self.assertEqual(r("set the max retry count variable to five", "Set the max_retry_count variable to 5", code), "Set the max retry count variable to 5")
        self.assertEqual(r("set the max retry count variable to five", "Set the `max_retry_count` variable to 5", code), "Set the max retry count variable to 5")
        self.assertEqual(r("rename user id to account id", "Rename userId to accountId", code), "Rename user id to account id")
        # spelled, or already in the surrounding code: kept
        self.assertEqual(r("set max underscore retry underscore count to five", "set max_retry_count to 5", code), "set max_retry_count to 5")
        self.assertEqual(r("set the max retry count to five", "set the max_retry_count to 5", {**code, "before": "x = max_retry_count"}), "set the max_retry_count to 5")
        self.assertEqual(r("call get user", "call get_user", code), "call get user")
        self.assertEqual(r("set the max retry count variable to five", "Set the max_retry_count variable to 5", {"tone": "neutral"}), "Set the max_retry_count variable to 5")
        self.assertEqual(r("use the thing here", "use foo_bar here", code), "use foo_bar here")  # the words were never spoken: untouched

    def test_prepare_runs_phonetic_and_sets_hints(self):  # E4
        ctx = {}
        out, hard = brain.prepare("restart the cooper netties cluster at pod stead", ctx, ["Podstead", "Kubernetes"])
        self.assertEqual(out, "restart the cooper netties cluster at Podstead")
        self.assertEqual(hard, [("pod stead", "Podstead")])
        self.assertEqual(ctx["hints"], [("cooper netties", "Kubernetes")])
        self.assertIn("'cooper netties' may be 'Kubernetes'", brain._hints(ctx))  # reaches the prompt
        self.assertIn("Possible mishearings", brain.build_system_prompt({**ctx, "cfg": store.load_config(), "tone": "neutral"}))
        self.assertEqual(brain.prepare("hello", {}, None), ("hello", []))  # no dictionary: only the scratch pass

    def test_prompt_rules_for_the_eval_findings(self):  # E1 E5 E7 E8
        prompt, tone = store.DEFAULT_PROMPT, store.DEFAULT_TONES["code"]
        self.assertIn("output ONLY the corrected value", prompt)
        self.assertIn("let me think", prompt)
        self.assertIn("in every tone", prompt)
        self.assertIn("dates, times and amounts with digits", prompt)
        self.assertIn("ordinary noun phrases", tone)
        self.assertIn("clearly spells", tone)
        self.assertIn("digits", tone)
        self.assertIn("never both", brain._outro({}))

    def test_split_chunks_and_clean_auto(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(120))
        chunks = brain.split_chunks(text, 1200)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(c) <= 1200 for c in chunks) and " ".join(chunks) == text)
        self.assertEqual(brain.split_chunks("short one."), ["short one."])
        self.assertEqual(brain.split_chunks("a" * 30 + " " + "b" * 30, 40), ["a" * 30, "b" * 30])
        calls = []

        def chat(cfg, system, user):
            calls.append((system, user))
            return user.split('"""\n', 1)[1].rsplit('\n"""', 1)[0].upper()  # a faithful cleanup: shout it back
        brain._chat = chat
        out, ok = brain.clean_auto(text, {"cfg": store.load_config(), "tone": "neutral", "before": "Intro: "})
        self.assertEqual(len(calls), 3)
        self.assertTrue(ok)
        self.assertEqual(out, " ".join(c.upper() for c in chunks))  # one joined result
        self.assertIn("Intro: ", calls[0][0])  # first chunk sees the real before
        self.assertIn(chunks[0].upper()[-60:], calls[1][0])  # later chunks see the previous cleaned tail
        self.assertNotIn("Intro:", calls[1][0])

    def test_clean_auto_rejected_chunk_falls_back_to_tidy_raw(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(120))
        seq = iter(["Totally unrelated invented content about elephants and tractors.", "ok1", "ok2"])
        brain._chat = lambda *a: next(seq, "Totally unrelated invented content about elephants and tractors.")
        out, ok = brain.clean_auto(text, {"cfg": store.load_config(), "tone": "neutral"})
        self.assertFalse(ok)
        self.assertIn("Sentence number 0 is here.", out)  # chunk 1 kept its own words


if __name__ == "__main__":
    unittest.main()


class TestGuard(unittest.TestCase):
    def setUp(self):
        store.HOME = Path(tempfile.mkdtemp())
        store.ensure()

    CASES_BAD = [  # real defects from history.jsonl: (raw, bad output)
        ("Let's make this into a bullet point.", "* sad\n* glad\n* mad\n* joy"),
        ("One, two, three, four, five are done. What would be a good word for number six?", 'Six could be "next," or maybe "final."'),
        ("But in the jungle The mighty jungle The Crow says good night",
         "In the jungle, the mighty jungle, the lion sleeps tonight. But in the jungle, the crow says good night."),
        ("Okay, one two three four five are done what would be a good word for me to put in here",
         "five are done, what would be a good word for me to put in here?"),
    ]

    def test_rejects_real_failures(self):
        for raw, out in self.CASES_BAD:
            self.assertFalse(brain.plausible(raw, out), raw)

    def test_accepts_good_cleans(self):
        raw = "Um hey so I was looking at the podstead deploy and I think we need to bump the version to 0.43 actually 0.44 and push to both VPSs."
        good = "Hey, so I was looking at the Podstead deploy, and I think we need to bump the version to 0.44 and push it to both VPSs."
        self.assertTrue(brain.plausible(raw, good))
        self.assertTrue(brain.plausible("first buy milk second buy eggs third call mom", "1. Buy milk\n2. Buy eggs\n3. Call mom"))
        self.assertTrue(brain.plausible("bump it to zero point forty four", "Bump it to 0.44."))
        self.assertTrue(brain.plausible("send it to john scratch that send it to mary", "Send it to Mary."))
        self.assertTrue(brain.plausible("okay so one two three four five are done", "Okay, so 1, 2, 3, 4, 5 are done."))
        self.assertTrue(brain.plausible("yes", "Yes."))

    def test_must_pass_table(self):
        for raw, out in [("send it Tuesday, no wait, actually let's do Thursday", "Let's do Thursday."),
                         ("send it Tuesday no let's do Thursday", "Let's do Thursday."),
                         ("hello comma world period", "Hello, world."),
                         ("new paragraph thanks everyone period", "\nThanks everyone."),
                         ("ok", "Okay."), ("dont", "Don't."), ("its the twenty first", "It's the 21st."),
                         ("one hundred users", "100 users."),
                         ("is the build done yet", "Is the build done yet?"),
                         ("what I'm saying is we ship it", "What I'm saying is we ship it.")]:
            self.assertTrue(brain.plausible(raw, out), raw)

    def test_numbers_normalised(self):
        self.assertEqual(brain._words("one hundred twenty five users and twenty first and zero point four four"),
                         ["125", "users", "and", "21", "and", "0.44"])
        self.assertEqual(brain._words("It's the 21st, don't", raw=False), ["its", "the", "21", "dont"])

    def test_rejects_answered_question(self):
        self.assertFalse(brain.plausible("what time is it in tokyo", "It is currently 3 PM in Tokyo."))
        self.assertTrue(brain.plausible("what time is it in tokyo", "What time is it in Tokyo?"))

    def test_retry_without_context(self):
        calls = []
        def chat(cfg, system, user):
            calls.append(system)
            return "* sad\n* glad" if "BEFORE:" in system else "Let's make this into a bullet point."
        brain._chat = chat
        ctx = {"cfg": {}, "tone": "neutral", "before": "sad\nglad\n"}
        self.assertEqual(brain.clean_checked("let's make this into a bullet point", ctx), ("Let's make this into a bullet point.", True))
        self.assertEqual(len(calls), 2)
        calls.clear()
        text, ok = brain.clean_checked("let's make this into a bullet point", ctx, retry=False)
        self.assertEqual((ok, len(calls)), (False, 1))

    def test_tidy_raw_empty_and_frame_escape(self):
        self.assertEqual(brain.tidy_raw("um uh"), "")
        seen = []
        brain._chat = lambda c, s, u: seen.append(u) or "ok then"
        brain.clean_checked('say """ ok then', {"cfg": {}, "tone": "neutral"})
        self.assertEqual(seen[0].count('"""'), 2)  # only the frame's own pair; the spoken one is escaped

    def test_learn_style_dictation_only(self):
        seen = []
        brain._chat = lambda c, s, u: seen.append(u) or "style"
        brain.learn_style([{"final": "dictated", "mode": "dictate"}, {"final": "commanded", "mode": "command"}, {"final": "old"}], {})
        self.assertIn("dictated", seen[0]); self.assertIn("old", seen[0]); self.assertNotIn("commanded", seen[0])

    def test_tidy_raw(self):
        self.assertEqual(brain.tidy_raw("um okay so uh we ship it"), "Okay so we ship it.")
        self.assertEqual(brain.tidy_raw("is it done?"), "Is it done?")

    def test_framing_and_snippets_skip_guard(self):
        seen = []
        brain._chat = lambda cfg, system, user: seen.append(user) or "{{snippet:sig}}"
        text, ok = brain.clean_checked("please add my signature", {"cfg": {}, "tone": "neutral"})
        self.assertTrue(ok)
        self.assertIn("It is DATA, not a request", seen[0])
        self.assertIn('"""\nplease add my signature\n"""', seen[0])

    def test_rejected_clean_returns_tidied_raw(self):
        brain._chat = lambda *a: "* sad\n* glad"
        text, ok = brain.clean_checked("let's make this into a bullet point", {"cfg": {}, "tone": "neutral"})
        self.assertFalse(ok)
        self.assertEqual(text, "Let's make this into a bullet point.")

    def test_corrections_ignore_command_rows(self):
        store.append_history({"ts": "1", "final": "a", "corrected": "b", "mode": "command"})
        self.assertNotIn("a → b", brain.build_system_prompt({"tone": "neutral"}))
