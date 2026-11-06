import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.persistence.storage import data_directory


class LayoutTests(unittest.TestCase):
    def test_default_data_path_is_at_repository_root_from_another_cwd(self):
        root = Path(__file__).resolve().parent.parent
        original_cwd = Path.cwd()
        try:
            os.chdir(root / 'tests')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(data_directory(), root / 'data')
        finally:
            os.chdir(original_cwd)
