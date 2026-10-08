from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from services.directory_scanner import DirectoryScanner, InventoryStore
from services.path_validator import PathValidator
from tools.count_files_tool import CountDirectoryFilesTool
from tools.duplicate_files_tool import DuplicateFilesTool
from tools.file_activity_tool import FileActivityTool
from tools.file_distribution_tool import FileDistributionTool


class DirectoryToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "a.txt").write_text("same", encoding="utf-8")
        (self.root / "b.txt").write_text("same", encoding="utf-8")
        (self.root / "c.json").write_text('{"ok": true}', encoding="utf-8")
        old = time.time() - 400 * 86400
        os.utime(self.root / "c.json", (old, old))
        validator = PathValidator([str(self.root)], ["/"])
        scanner = DirectoryScanner(
            validator,
            max_depth=5,
            max_files=100,
            timeout_seconds=5,
            include_hidden=False,
        )
        self.inventories = InventoryStore(scanner, cache_seconds=60)

    def tearDown(self):
        self.temp.cleanup()

    def test_count_and_distribution(self):
        count = CountDirectoryFilesTool(self.inventories).invoke(str(self.root))
        distribution = FileDistributionTool(self.inventories).invoke(str(self.root))
        self.assertTrue(count.ok)
        self.assertEqual(3, count.data["file_count"])
        self.assertEqual(2, distribution.data["type_count"])

    def test_duplicate_detection_uses_hash(self):
        result = DuplicateFilesTool(
            self.inventories, max_hash_file_bytes=1024
        ).invoke(str(self.root))
        self.assertTrue(result.ok)
        self.assertEqual(1, result.data["duplicate_group_count"])
        self.assertEqual({"a.txt", "b.txt"}, set(result.data["groups"][0]["files"]))

    def test_activity(self):
        result = FileActivityTool(
            self.inventories,
            recent_days=30,
            stale_days=365,
            large_file_bytes=4,
        ).invoke(str(self.root))
        self.assertTrue(result.ok)
        self.assertEqual(1, result.data["stale_file_count"])
        self.assertGreaterEqual(len(result.data["large_files"]), 2)


if __name__ == "__main__":
    unittest.main()

