import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from local_auth import LocalAuth, LocalAuthError, SESSION_SECONDS


class LocalAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = object.__new__(LocalAuth)
        self.auth.binary = Path("/unused/touchid-helper")
        self.auth._sessions = {}
        self.auth._lock = threading.Lock()
        self.auth._prompt_lock = threading.Lock()
        self.auth._last_attempt = 0.0

    @patch("local_auth.time.monotonic", return_value=100.0)
    @patch("local_auth.subprocess.run", return_value=subprocess.CompletedProcess([], 1))
    def test_rejected_biometric_creates_no_session(self, run, _clock):
        with self.assertRaises(LocalAuthError):
            self.auth.unlock()
        self.assertEqual(self.auth._sessions, {})
        run.assert_called_once_with([str(self.auth.binary), "authenticate"],
                                    capture_output=True, timeout=100, check=False)

    @patch("local_auth.time.monotonic", return_value=100.0)
    @patch("local_auth.subprocess.run", return_value=subprocess.CompletedProcess([], 0))
    def test_token_expires_and_can_be_revoked(self, _run, clock):
        token = self.auth.unlock()
        self.assertTrue(self.auth.valid(token))
        self.assertFalse(self.auth.valid("wrong"))
        self.auth.lock(token)
        self.assertFalse(self.auth.valid(token))
        clock.return_value = 103.0
        token = self.auth.unlock()
        clock.return_value = 103.0 + SESSION_SECONDS
        self.assertFalse(self.auth.valid(token))

    @patch("local_auth.time.monotonic", return_value=100.0)
    def test_only_activity_renews_and_late_notifications_cannot_resurrect_expired_session(self, clock):
        token = self.auth._create_session('touch_id')
        clock.return_value = 350.0
        self.assertEqual(self.auth.session_info(token)['remaining_seconds'], 50)
        self.assertTrue(self.auth.valid(token))
        self.assertEqual(self.auth.session_info(token)['remaining_seconds'], 50)
        self.auth.record_activity(token)
        clock.return_value = 450.0
        self.assertEqual(self.auth.session_info(token)['remaining_seconds'], 200)
        clock.return_value = 650.0
        self.assertIsNone(self.auth.record_activity(token))
        self.assertIsNone(self.auth.session_info(token))

    @patch("local_auth.time.monotonic", return_value=100.0)
    def test_activity_age_preserves_true_idle_deadline_and_locked_tokens_stay_revoked(self, clock):
        token = self.auth._create_session('password')
        clock.return_value = 200.0
        renewed = self.auth.record_activity(token, 15000)
        self.assertEqual(renewed['remaining_seconds'], 285)
        # An older request must not shorten the newer deadline.
        self.auth.record_activity(token, 90000)
        self.assertEqual(self.auth.session_info(token)['remaining_seconds'], 285)
        for invalid in [-1, True, 1.5, '1', SESSION_SECONDS * 1000]:
            with self.assertRaises(LocalAuthError): self.auth.record_activity(token, invalid)
        self.auth.lock(token)
        self.assertIsNone(self.auth.record_activity(token))


if __name__ == "__main__":
    unittest.main()
