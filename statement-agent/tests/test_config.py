import os
import unittest
from unittest.mock import patch

from config import get_settings


class ConfigTest(unittest.TestCase):
    def test_get_settings_reads_environment_lazily(self) -> None:
        with patch.dict(os.environ, {"EXTRACTION_ENDPOINT": "synthetic-endpoint-a"}):
            self.assertEqual(get_settings().extraction_endpoint, "synthetic-endpoint-a")
        with patch.dict(os.environ, {"EXTRACTION_ENDPOINT": "synthetic-endpoint-b"}):
            self.assertEqual(get_settings().extraction_endpoint, "synthetic-endpoint-b")


if __name__ == "__main__":
    unittest.main()
