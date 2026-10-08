import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest import mock
from murmur import brain

LIST = "So we got item one, item two, and item three:\n* Grapes\n* Pineapples\n* Pears\n* Peaches\n* Plums\n* A bunch of other fruits."
LESS = LIST.rsplit("\n", 1)[0]
OK = brain.command_ok


class Guard(unittest.TestCase):
    def test_real_failures_rejected(self):
        self.assertFalse(OK(LIST, "Remove a bunch of other fruits.", " This is a list of fruits."))  # reply instead of edit
        self.assertFalse(OK(LIST, "Remove a bunch of roots.", " Remove a bunch of roots."))  # echoed the instruction
        self.assertFalse(OK("Toung", "Add Toung to the dictionary.", "Toung added to the dictionary."))  # answered conversationally
        self.assertFalse(OK(LIST, "make this formal", "Here is the formal version."))

    def test_dropped_content_rejected(self):
        self.assertFalse(OK(LIST, "fix the spelling", "Fruits."))

    def test_good_edits_accepted(self):
        self.assertTrue(OK(LIST, "Remove a bunch of other fruits.", LESS))
        self.assertTrue(OK(LIST, "delete the last item", LESS))
        self.assertTrue(OK("hey can u send me the report by friday thx", "make this formal",
                           "Hello, could you please send me the report by Friday? Thank you."))
        self.assertTrue(OK(LIST, "summarize this", "A list of five fruits."))
        self.assertTrue(OK("The meeting is at noon in the main office.", "translate to Spanish", "La reunion es a mediodia en la oficina principal."))
        self.assertTrue(OK("This is a test of the thing.", "fix capitalization", "This is a test of the thing."))  # starts like a reply, is the text
        self.assertTrue(OK("", "write a short hello", "Hello there, how are you?"))  # generation
        self.assertTrue(OK("ok", "make it uppercase", "OK"))  # too little to judge

    def test_edges_stripped_only_when_selection_had_none(self):
        self.assertEqual(brain.tidy_edges(" hi ", "hello"), "hi")
        self.assertEqual(brain.tidy_edges(" hi", " hello"), " hi")


class Checked(unittest.TestCase):
    def run_with(self, outs):
        with mock.patch.object(brain, "_chat", side_effect=outs) as c, mock.patch.object(brain.store, "load_snippets", return_value={}):
            return brain.rewrite_checked(LIST, "Remove a bunch of other fruits.", {"cfg": {"tones": {}}}), c  # a real cfg: never read config.toml

    def test_retry_then_accept(self):
        (out, ok), c = self.run_with([" This is a list of fruits.", LESS])
        self.assertTrue(ok); self.assertEqual(out, LESS); self.assertEqual(c.call_count, 2)
        self.assertIn("Output ONLY the edited text", c.call_args.args[1])

    def test_both_rejected_returns_nothing(self):
        (out, ok), _ = self.run_with([" This is a list of fruits."] * 2)
        self.assertEqual((out, ok), ("", False))


class NoTarget(unittest.TestCase):  # Ethan 10-08: "Make this into a list" with nothing read -> "Please provide the text..."
    def test_edit_of_this_needs_a_target(self):
        for ins in ("Make this into a list.", "turn it into bullets", "translate that to Spanish", "please fix the text"):
            self.assertTrue(brain.needs_target(ins), ins)
        for ins in ("make a list of groceries", "write a haiku about cats", "draft a reply saying I'm late"):
            self.assertFalse(brain.needs_target(ins), ins)

    def test_asking_for_input_is_never_pasted(self):
        self.assertFalse(brain.command_ok("", "Make this into a list.", "Please provide the text you would like me to make into a list."))
        self.assertFalse(brain.command_ok("a b c d e", "make a list", "Could you provide the text to format?"))
        self.assertTrue(brain.command_ok("", "write a reply", "Sounds good, see you at five."))


class ReviewCases(unittest.TestCase):  # review 10-07: legitimate edits the first guard rejected, and what must still fail
    def test_generation_may_open_like_a_reply(self):
        for ins, out in (("write a text saying I am running late", "I'm running late, be there soon."),
                         ("apologize to Sam for missing the call", "Sorry I missed the call, Sam."),
                         ("write a note that this is a draft", "This is a draft."), ("say okay", "Okay")):
            self.assertTrue(brain.command_ok("", ins, out), out)

    def test_rewrites_that_open_like_a_reply(self):
        sel = "Can you review the deploy checklist before the release goes out tomorrow morning?"
        self.assertTrue(brain.command_ok(sel, "reply to this", "Sure, I will take a look at the deploy checklist today."))
        self.assertTrue(brain.command_ok("We shipped the release and closed the tickets.", "first person",
                                         "I have shipped the release and closed the tickets."))

    def test_transformations_without_the_usual_keywords(self):
        sel = "The quarterly report is ready for review and the numbers look strong this time."
        for ins, out in (("put it in Spanish", "El informe trimestral está listo para revisión y las cifras se ven sólidas."),
                         ("turn this into a question", "Is the quarterly report ready, and do the numbers look strong?"),
                         ("make it punchier", "Quarterly report: ready. Numbers: strong."),
                         ("make this a haiku", "Report waits for eyes / numbers standing tall and proud / quarter well spent now")):
            self.assertTrue(brain.command_ok(sel, ins, out), ins)

    def test_still_rejected(self):
        sel = "So we got item one, item two, and item three:\n* Grapes\n* Pineapples\n* Pears\n* Peaches\n* Plums\n* A bunch of other fruits."
        self.assertFalse(brain.command_ok(sel, "Remove a bunch of other fruits.", " This is a list of fruits."))
        self.assertFalse(brain.command_ok(sel, "Remove a bunch of roots.", " Remove a bunch of roots."))
        self.assertFalse(brain.command_ok(sel, "make it formal", "Here is the revised version: So we have items one to three."))
        self.assertFalse(brain.command_ok(sel, "delete the last item", "Grapes."))  # a targeted edit that gutted the list


class Paraphrase(unittest.TestCase):  # Ethan 10-08: "make this formal" on a triple-clicked line was refused twice
    def test_formal_may_replace_every_word(self):
        sel = "1. The way to do things is the positive way.\n"
        self.assertTrue(brain.command_ok(sel, "Make this formal.", "The preferred methodology is the affirmative approach."))
        self.assertTrue(brain.command_check(sel, "make this formal", "Yes.").startswith("length="))  # gutted: still refused
        self.assertEqual(brain.command_check(sel, "make it casual", "This is a list of methods."), "reply")

    def test_fix_grammar_still_needs_the_words(self):
        sel = "their going too the store tomorrow to buy apples and pears"
        self.assertIsNotNone(brain.command_check(sel, "fix the grammar", "I cannot help with that request today, sorry about it."))


if __name__ == "__main__":
    unittest.main()
