import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from murmur import brain, phonetic


class OverMatch(unittest.TestCase):  # Ethan's reviewed takes, 10-09
    def hints(self, raw, terms=("Podstead",)):
        return phonetic.phonetic_fix(raw, list(terms), brain.COMMON)[2]

    def test_a_weak_match_needs_the_same_first_letter(self):  # "The quote said" pasted as "Podstead said"
        self.assertEqual(self.hints("Boates said, Ship it Friday, end quote."), [])
        self.assertEqual(self.hints("The pot set is up."), [("pot set", "Podstead")])  # same letter: still a hint
        self.assertEqual(self.hints("Cooper Netties is down.", ["Kubernetes"]), [("Cooper Netties", "Kubernetes")])  # c = k

    def test_a_heard_plural_hints_the_plural_term(self):  # "two Podsteads" came out "two Podstead"
        self.assertEqual(self.hints("We run two pods heads now."), [("pods heads", "Podsteads")])
        self.assertEqual([t for _, t in self.hints("my depgard works", ["depguard"])], ["depguard"])  # the -s isn't its own


if __name__ == "__main__":
    unittest.main()
