import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from settings import ConfigError, get_openai_api_key


class SettingsTests(unittest.TestCase):
    def test_reads_key_from_private_env_file_without_requiring_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("# local\nOPENAI_API_KEY=synthetic-key\n", encoding="utf-8")
            env_file.chmod(0o600)
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                self.assertEqual(get_openai_api_key(env_file), "synthetic-key")
                env_file.write_text("OPENAI_API_KEY=changed-key\n", encoding="utf-8")
                self.assertEqual(get_openai_api_key(env_file), "changed-key")

    def test_process_variable_takes_precedence_and_open_permissions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OPENAI_API_KEY=file-key\n", encoding="utf-8")
            env_file.chmod(0o644)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "process-key"}):
                self.assertEqual(get_openai_api_key(env_file), "process-key")
            if os.name != "nt":
                with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                    with self.assertRaises(ConfigError):
                        get_openai_api_key(env_file)

    def test_missing_or_empty_key_keeps_categorization_unconfigured(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                self.assertEqual(get_openai_api_key(env_file), "")
                env_file.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
                env_file.chmod(0o600)
                self.assertEqual(get_openai_api_key(env_file), "")


if __name__ == "__main__":
    unittest.main()
