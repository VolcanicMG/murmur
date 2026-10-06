import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from murmur import card, theme


class T(unittest.TestCase):
    def test_blend(self):
        self.assertEqual(theme.blend("#ffffff", "#000000", 0.5), "#808080")
        self.assertEqual(theme.blend("#8E93F7", "#161826", 1.0), "#8e93f7")
        self.assertEqual(theme.blend("#8E93F7", "#161826", 0.0), "#161826")
        self.assertEqual(theme.on_fill(0.06), theme.blend("#FFFFFF", theme.FILL, 0.06))

    def test_bar_heights(self):
        n = 64
        live = card.bar_heights(1.0, 1234, "live", n)
        self.assertEqual(len(live), n)
        self.assertTrue(all(2 <= h <= 28 for h in live))
        self.assertGreater(live[n // 2], live[0])  # centre-weighted
        quiet = card.bar_heights(0.0, 1234, "live", n)
        self.assertTrue(all(abs(h - 2) < 1e-9 for h in quiet))
        self.assertTrue(all(h < 3 for h in card.bar_heights(0.9, 5, "flat", n)))
        self.assertGreater(max(card.bar_heights(0, 700, "wave", n)), 4)

    def test_norm_level(self):
        self.assertEqual(card.norm_level(0), 0)
        self.assertLess(card.norm_level(0.02), card.norm_level(0.2))
        self.assertLessEqual(card.norm_level(5), 1.0)
        self.assertGreater(card.norm_level(0.05), 0.4)

    def test_place(self):
        work = (0, 0, 1920, 1080)
        # fits above the field: bottom of card = field top - 16
        x, y, up = card.place((560, 170), (400, 600, 900, 640), work)
        self.assertEqual((x, y, up), (400, 600 - 16 - 170, False))
        # no room above: flips below with the tail up
        x, y, up = card.place((560, 170), (400, 60, 900, 100), work)
        self.assertEqual((x, y, up), (400, 116, True))
        # clamped to the right edge with a 12 px margin
        x, y, up = card.place((560, 170), (1800, 600, 1900, 640), work)
        self.assertEqual(x, 1920 - 12 - 560)

    def test_misc(self):
        self.assertEqual(card.ellipsize("short"), "short")
        self.assertEqual(len(card.ellipsize("x" * 50, 28)), 28)
        pts = theme.rounded_points((0, 0, 100, 40), 20)
        self.assertEqual(len(pts), 4 * 9 * 2)
        self.assertTrue(all(0 <= v <= 100 for v in pts[::2]))
        for name, st in card.STATES.items():
            self.assertEqual(len(st), 9, name)

    @unittest.skipUnless(__import__("importlib").util.find_spec("PIL"), "PIL not installed")
    def test_tray_images(self):
        from murmur import tray
        for state in tray.COLORS:
            im = tray.tray_image(state)
            self.assertEqual((im.size, im.mode), ((64, 64), "RGBA"))
            self.assertTrue(tray.TOOLTIPS[state].format(key="right ctrl"))


if __name__ == "__main__":
    unittest.main()
