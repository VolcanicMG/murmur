import sys, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
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
        half = card.bar_heights(0.5, 0, "progress", n)  # loading bar: the done half stands, the rest is flat
        self.assertTrue(all(h > 3 for h in half[:n // 2]) and all(abs(h - 2) < 1e-9 for h in half[n // 2:]))
        self.assertEqual(card.STATES["loading"][3], "progress")
        self.assertNotIn("600 MB", card.STATES["loading"][7])  # the model ships with the build

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

    def test_place_pane(self):
        work = (0, 0, 1920, 1080)
        # whole editor pane with no caret: inside it near the bottom-left, never flipped or clamped to the screen bottom
        x, y, up = card.place((560, 170), (300, 40, 1500, 900), work, kind="pane")
        self.assertEqual((x, y, up), (316, 900 - 16 - 170, False))

    def test_pick_rect(self):
        ctl = (300, 40, 1500, 900)
        boom = lambda: 1 / 0
        self.assertEqual(card.pick_rect([lambda: None, boom, lambda: (400, 200, 402, 220)], ctl), ((400, 200, 402, 220), "caret"))
        self.assertEqual(card.pick_rect([lambda: (400, 200, 402, 400), lambda: (0, 0, 5, 5)], ctl), (ctl, "pane"))  # too tall / off-control
        self.assertEqual(card.pick_rect([], (0, 0, 800, 100)), ((0, 0, 800, 100), "field"))

    def test_misc(self):
        self.assertEqual(card.ellipsize("short"), "short")
        self.assertEqual(len(card.ellipsize("x" * 50, 28)), 28)
        pts = theme.rounded_points((0, 0, 100, 40), 20)
        self.assertEqual(len(pts), 4 * 9 * 2)
        self.assertTrue(all(0 <= v <= 100 for v in pts[::2]))
        for name, st in card.STATES.items():
            self.assertEqual(len(st), 9, name)

    @unittest.skipUnless(__import__("importlib").util.find_spec("PIL"), "PIL not installed")
    def test_painter(self):
        from murmur.cardpaint import Painter, View
        p = Painter(1.0)
        lay = p.layout("transcribing", "ok just pushed the new build to staging " * 3, "lunch")
        self.assertGreater(len(lay.lines), 1)
        lst = p.layout("transcribing", "* Fix the flicker.\n* Test the hotkeys.\nBullet three,", "ship")
        self.assertEqual([" ".join(w.strip() for w, _ in l) for l in lst.lines], ["* Fix the flicker.", "* Test the hotkeys.", "Bullet three, ship"])
        self.assertEqual(lay.ch, 52 + lay.th + 4 + 10 + 30 + 14)
        v = View("Listening", "#8E93F7", "Slack", False, 43, 0.5, 1.0)
        img = p.draw(lay, v)
        self.assertEqual(img.size, (560 + 2 * p.pad, lay.ch + 2 * p.pad))
        self.assertLessEqual(p.pad, 40)
        self.assertEqual(img.getpixel((0, 0))[3], 0)  # window corner is clear
        self.assertGreater(img.getpixel((p.pad + 280, p.pad + 5))[3], 200)  # card body is near-opaque
        self.assertIs(p.draw(lay, v), img)  # unchanged frame -> same object (no UpdateLayeredWindow)
        self.assertNotEqual(p.draw(lay, v._replace(lvl=0.9, now=1.1)).tobytes(), img.tobytes())
        err = p.layout("error", "", "")
        p.draw(err, View("No microphone", "#F0756A", "", True, 43, 0, 0))
        l, t, r, b = p.retry_box
        self.assertEqual((round(b - t), r), (32, 560 - 14))
        p.draw(lay, v)
        self.assertIsNone(p.retry_box)

    @unittest.skipUnless(__import__("importlib").util.find_spec("pystray"), "pystray not installed")
    def test_pystray_icon_takes_every_state(self):
        import pystray
        from murmur import tray
        icon = pystray.Icon("t", tray.tray_image("idle"))  # built, not run
        for state in tray.COLORS:
            img = tray.tray_image(state).copy()
            icon.icon = img
            self.assertIs(icon.icon, img)
            self.assertEqual(icon.icon.size, (64, 64))
        self.assertNotEqual(tray.tray_image("recording").getpixel((10, 32)), tray.tray_image("idle").getpixel((10, 32)))

    @unittest.skipUnless(__import__("importlib").util.find_spec("PIL"), "PIL not installed")
    def test_tray_images(self):
        from murmur import tray
        for state in tray.COLORS:
            im = tray.tray_image(state)
            self.assertEqual((im.size, im.mode), ((64, 64), "RGBA"))
            self.assertTrue(tray.TOOLTIPS[state].format(key="right ctrl"))


if __name__ == "__main__":
    unittest.main()


class ModelDir(unittest.TestCase):
    def test_bundled_model_dir_and_fetch_dest(self):
        import types
        fake = types.ModuleType("onnx_asr"); calls = []
        fake.load_model = lambda name, path=None, quantization=None: calls.append((name, path, quantization))
        saved = sys.modules.get("onnx_asr"); sys.modules["onnx_asr"] = fake
        sys.modules.pop("murmur.stt", None)
        import murmur; murmur.__dict__.pop("stt", None)  # else `from murmur import stt` returns another test's copy
        try:
            from murmur import stt
            self.assertEqual(stt.model_dir().parts[-3:], ("packaging", "models", "parakeet"))
            stt.load_model({"stt": {"model": stt.DEFAULT_MODEL, "quantization": stt.DEFAULT_QUANT}})
            self.assertEqual(calls[-1], (stt.DEFAULT_MODEL, stt.model_dir() if stt.model_dir().is_dir() else None, "int8"))
            stt.load_model({"stt": {"model": "other", "quantization": "int8"}})
            self.assertIsNone(calls[-1][1])  # a non-default model never reads the bundled folder
            stt.load_model({"stt": {"model": stt.DEFAULT_MODEL, "quantization": "int8", "path": "D:/models/mine"}})
            self.assertEqual(calls[-1], (None, "D:/models/mine", "int8"))  # a custom folder: onnx-asr reads the type from its config.json
            self.assertEqual(stt.fetch("/tmp/x").name, "x"); self.assertEqual(Path(calls[-1][1]), Path("/tmp/x"))  # separators differ on Windows
        finally:
            sys.modules.pop("murmur.stt", None)
            if saved: sys.modules["onnx_asr"] = saved
            else: sys.modules.pop("onnx_asr", None)



class Appear(unittest.TestCase):
    def test_appear_puts_the_card_back_on_top(self):
        import murmur
        fake = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"murmur.win": fake}), mock.patch.object(murmur, "win", fake, create=True), \
                mock.patch.object(card, "win32_hwnd", return_value=42):
            obj = SimpleNamespace(root=object())
            card.Card._appear(obj)
            fake.keep_on_top.side_effect = OSError("access denied")
            card.Card._appear(obj)  # a failed raise must never stop the card from showing
        self.assertEqual(fake.keep_on_top.call_args_list, [mock.call(42), mock.call(42)])
        self.assertTrue(obj.visible)
