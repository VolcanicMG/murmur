import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with mock.patch.dict(sys.modules, {"onnx_asr": mock.MagicMock()}):
    from murmur import stt


class ModelName(unittest.TestCase):
    def test_the_stale_v3_default_loads_v2(self):  # every saved config named v3, so v2 never loaded
        self.assertEqual(stt.model_name({"model": "nemo-parakeet-tdt-0.6b-v3"}), stt.DEFAULT_MODEL)
        self.assertEqual(stt.model_name({}), stt.DEFAULT_MODEL)
        self.assertEqual(stt.model_name({"model": "nemo-canary-1b-v2"}), "nemo-canary-1b-v2")


if __name__ == "__main__":
    unittest.main()
