import re, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import murmur


class T(unittest.TestCase):
    def test_version_is_semver(self):
        self.assertRegex(murmur.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
