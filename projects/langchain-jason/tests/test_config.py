from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config_loader import load_settings
from core.prompt_registry import PromptRegistry


class ConfigTest(unittest.TestCase):
    def test_cli_override_has_highest_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.yaml"
            path.write_text("app:\n  default_mode: lcel\n", encoding="utf-8")
            with patch.dict(os.environ, {"LANGCHAIN_JASON_MODE": "react"}):
                settings = load_settings(path, {"app.default_mode": "hybrid"})
            self.assertEqual("hybrid", settings.get("app.default_mode"))

    def test_prompt_registry_reads_nested_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompts.yaml"
            path.write_text("system:\n  react: 'hello {name}'\n", encoding="utf-8")
            prompts = PromptRegistry(path)
            self.assertEqual("hello Codex", prompts.get("system.react", name="Codex"))


if __name__ == "__main__":
    unittest.main()

