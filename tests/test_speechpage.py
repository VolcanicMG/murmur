import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import speech, speechpage as sp


class T(unittest.TestCase):
    def test_every_goal_has_a_metric_row(self):
        self.assertEqual(set(sp.METRICS), set(speech.VALID_GOALS))

    def test_improving(self):
        self.assertTrue(sp.improving("fillers", 3, 5, -40))
        self.assertFalse(sp.improving("fillers", 6, 5, 20))
        self.assertIsNone(sp.improving("fillers", 5, 5, 0))
        self.assertIsNone(sp.improving("fillers", None, 5, None))
        self.assertIsNone(sp.improving("pace", 145, 190, -23))  # inside the 130-160 band: steady, not "improved"
        self.assertTrue(sp.improving("pace", 170, 200, -15))  # closer to the band
        self.assertFalse(sp.improving("pace", 200, 170, 17))

    def test_delta_text(self):
        self.assertEqual(sp.delta_text("fillers", 3, 5, -34.4), "\u25bc 34%")
        self.assertEqual(sp.delta_text("fillers", 6, 5, 20), "\u25b2 20%")
        self.assertEqual(sp.delta_text("pace", 140, 170, -17), "steady")
        self.assertEqual(sp.delta_text("fillers", 5, 5, 0.1), "no change")
        self.assertEqual(sp.delta_text("fillers", 5, None, None), "")

    def test_fmt_and_filter(self):
        self.assertEqual(sp.fmt(3.14159, "fillers"), "3.1")
        self.assertEqual(sp.fmt(0.2, "clarity"), "0.20")
        self.assertEqual(sp.fmt(None), "\u2013")
        rows = [{"app": "a"}, {"app": "b"}]
        self.assertEqual(sp.rows_for(rows, "a"), [{"app": "a"}])
        self.assertEqual(sp.rows_for(rows, "All apps"), rows)


if __name__ == "__main__":
    unittest.main()
