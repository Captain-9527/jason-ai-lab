from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.exceptions import PathValidationError
from services.path_validator import PathValidator


class PathValidatorTest(unittest.TestCase):
    def test_rejects_path_outside_allowed_root(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            validator = PathValidator([allowed], ["/"])
            with self.assertRaises(PathValidationError):
                validator.validate_directory(outside)

    def test_resolves_directory_inside_allowed_root(self):
        with tempfile.TemporaryDirectory() as allowed:
            child = Path(allowed) / "child"
            child.mkdir()
            validator = PathValidator([allowed], ["/"])
            self.assertEqual(child.resolve(), validator.validate_directory(str(child)))

    def test_rejects_symlink_that_escapes_allowed_root(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            link = Path(allowed) / "outside-link"
            link.symlink_to(outside, target_is_directory=True)
            validator = PathValidator([allowed], ["/"])
            with self.assertRaises(PathValidationError):
                validator.validate_directory(str(link))


if __name__ == "__main__":
    unittest.main()
