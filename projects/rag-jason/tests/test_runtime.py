"""发布配置与样例数据的离线回归检查。"""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


class RuntimeTest(unittest.TestCase):
    def test_paths_are_independent_of_working_directory(self):
        env = {**os.environ, "PYTHONPATH": str(PROJECT)}
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-c", "from src.runtime import DATA_ROOT; print(DATA_ROOT)"],
                cwd=cwd, env=env, capture_output=True, text=True, check=True,
            )
        self.assertEqual(result.stdout.strip(), str(PROJECT / "data/stock_data"))

    def test_environment_overrides_local_dotenv(self):
        # 将模块复制到隔离目录，避免读写使用者的真实 .env。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/runtime.py").write_bytes((PROJECT / "src/runtime.py").read_bytes())
            (root / ".env").write_text("RAG_TEST_EXISTING=file\nRAG_TEST_NEW=loaded\n")
            env = {**os.environ, "PYTHONPATH": str(root), "RAG_TEST_EXISTING": "shell"}
            env.pop("RAG_TEST_NEW", None)
            result = subprocess.run(
                [sys.executable, "-c", "import src.runtime, os; print(os.environ['RAG_TEST_EXISTING'], os.environ['RAG_TEST_NEW'])"],
                cwd=root, env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(result.stdout.strip(), "shell loaded")

    def test_examples_match_expected_input_format(self):
        with (PROJECT / "examples/subset.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(rows)
        self.assertEqual(len({r["sha1"] for r in rows}), len(rows))
        for row in rows:
            self.assertTrue((PROJECT / "examples" / (row["file_name"] + ".md")).is_file())
        questions = json.loads((PROJECT / "examples/questions.json").read_text())
        self.assertTrue(questions)
        for question in questions:
            self.assertTrue(question["text"])
            self.assertIn(question["kind"], ("number", "string"))


if __name__ == "__main__":
    unittest.main()
