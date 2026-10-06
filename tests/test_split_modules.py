import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import brain, learn, prepass


class Reexports(unittest.TestCase):
    def test_brain_still_exposes_what_callers_import(self):
        for name in ("apply_scratch", "has_scratch", "find_cue", "spoken_command", "spoken_forms", "correct_numbers", "spoken_breaks",
                     "spoken_addresses", "restore_prose_identifiers", "COMMANDS", "COMMON", "FILLERS", "_words"):
            self.assertIs(getattr(brain, name), getattr(prepass, name), name)
        for name in ("clean_word", "learn_dictionary", "is_redictation", "redictation_terms", "detect_edit", "learn_from_edit",
                     "edit_is_a_correction"):
            self.assertIs(getattr(brain, name), getattr(learn, name), name)

    def test_the_new_modules_work_on_their_own(self):
        self.assertEqual(prepass.apply_scratch("A. B, scratch that. C."), "A. C.")
        self.assertEqual(prepass._words("back in nineteen ninety nine"), ["back", "in", "1999"])
        self.assertEqual(learn.learn_dictionary("hey podstead", "hey Podstead"), ["Podstead"])
        self.assertEqual(learn.detect_edit("talk to john", "talk to Jon"), "talk to Jon")

    def test_no_import_cycle_back_into_brain(self):
        for mod in (prepass, learn):
            self.assertNotIn("brain", getattr(mod, "__dict__", {}))


if __name__ == "__main__":
    unittest.main()
