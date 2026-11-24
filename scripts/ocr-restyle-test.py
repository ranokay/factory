# /// script
# requires-python = ">=3.12"
# dependencies = ["hypothesis==6.170.17"]
# ///

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ocr_restyle_cli_test import CommandLineTests
from ocr_restyle_unit_test import PropertyTests, RestyleTests

__all__ = ["CommandLineTests", "PropertyTests", "RestyleTests"]

if __name__ == "__main__":
    unittest.main()
