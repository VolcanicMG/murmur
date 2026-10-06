import importlib.util, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_PIL = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(HAS_PIL, "PIL not installed")
class T(unittest.TestCase):
    def setUp(self):
        from murmur import render
        self.r = render

    def has_soft_edge(self, im):
        return any(0 < im.getpixel((x, y))[3] < 255 for x in range(im.width) for y in range(im.height))

    def test_rounded(self):
        im = self.r.rounded((60, 30), 10, "#ff0000")
        self.assertEqual((im.size, im.mode), ((60, 30), "RGBA"))
        self.assertEqual(im.getpixel((0, 0))[3], 0)  # corner transparent
        self.assertEqual(im.getpixel((30, 15)), (255, 0, 0, 255))  # centre = fill
        self.assertTrue(self.has_soft_edge(im))  # anti-aliased
        self.assertIs(self.r.rounded((60, 30), 10, "#ff0000"), im)  # cached

    def test_alpha_fill_and_outline(self):
        im = self.r.rounded((60, 30), 10, ("#102030", 0.5), ("#ffffff", 0.5), 2)
        self.assertEqual(im.getpixel((30, 15))[3], 128)
        self.assertLessEqual(abs(im.getpixel((30, 15))[0] - 16), 1)  # premultiplied rounding
        self.assertGreater(im.getpixel((30, 0))[0], 100)  # border row is lighter than the fill

    def test_pill_circle_bar(self):
        p = self.r.pill((80, 32), "#8e93f7")
        self.assertEqual(p.getpixel((0, 0))[3], 0)
        self.assertEqual(p.getpixel((40, 16))[:3], (0x8e, 0x93, 0xf7))
        self.assertTrue(self.has_soft_edge(self.r.circle(24, "#ffffff")))
        self.assertEqual(self.r.bar(3, 20, "#ffffff").size, (3, 20))
        self.assertEqual(self.r.circle(24, "#ffffff").getpixel((12, 12))[3], 255)
        c = self.r.chevron((10, 6), "#a8aab8")
        self.assertEqual(c.size, (10, 6)); self.assertTrue(self.has_soft_edge(c)); self.assertEqual(c.getpixel((0, 5))[3], 0)

    def test_outline_only_pill_is_hollow(self):
        p = self.r.pill((80, 32), None, ("#ffffff", 0.14))
        self.assertEqual(p.getpixel((40, 16))[3], 0)
        self.assertGreater(p.getpixel((40, 0))[3], 0)

    def test_tail(self):
        down = self.r.tail(14, ("#1a1c2a", 0.95), ("#ffffff", 0.1))
        self.assertEqual(down.height * 2, down.width)
        self.assertGreater(down.getpixel((down.width // 2, 2))[3], 200)  # solid at the card edge
        self.assertEqual(down.getpixel((0, down.height - 1))[3], 0)
        self.assertTrue(self.has_soft_edge(down))
        up = self.r.tail(14, ("#1a1c2a", 0.95), ("#ffffff", 0.1), True)
        self.assertGreater(up.getpixel((up.width // 2, up.height - 3))[3], 200)

    def test_shadow(self):
        sh = self.r.shadow((200, 100), 24, 30, 10, 0.5)
        p = self.r.shadow_pad(30, 10)
        self.assertEqual(sh.size, (200 + 2 * p, 100 + 2 * p))
        self.assertEqual(sh.getpixel((0, 0))[3], 0)
        self.assertGreater(sh.getpixel((sh.width // 2, sh.height // 2))[3], 100)
        self.assertEqual(self.r.shadow_pad(30, 10), 40)

    def test_toggle(self):
        cols = ("#333333", "#8e93f7", "#e3e4ea", "#16182a")
        off, on = self.r.toggle(0, (44, 24), cols), self.r.toggle(1, (44, 24), cols)
        self.assertEqual(off.size, (44, 24))
        self.assertNotEqual(off.getpixel((8, 12)), on.getpixel((8, 12)))  # knob left when off, track left when on
        self.assertEqual(on.getpixel((35, 12))[:3], (0x16, 0x18, 0x2a))  # on-knob on the right

    def test_font(self):
        self.assertGreater(self.r.font("ui", 18).getlength("hello"), 10)
        self.assertIs(self.r.font("mono", 11), self.r.font("mono", 11))


if __name__ == "__main__":
    unittest.main()
