import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_auth import LocalAuth, LocalAuthError, SESSION_SECONDS


class PasswordAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.private = Path(self.directory.name)
        with patch.object(LocalAuth, '_prepare'):
            self.auth = LocalAuth(self.private / 'unused.swift', self.private)
        self.token = self.auth._create_session('touch_id')
        self.password = 'Senha somente de teste 42!'

    def tearDown(self):
        self.directory.cleanup()

    def configure(self):
        self.auth.configure_password(self.token, self.password)

    def test_hash_is_private_and_identity_survives_restart_without_touch_id(self):
        principal = self.auth.principal(self.token)['principal_id']
        self.configure()
        raw = self.auth.identity_path.read_text()
        self.assertNotIn(self.password, raw)
        credential = json.loads(raw)['password']
        self.assertEqual(credential['algorithm'], 'scrypt-v1')
        self.assertEqual(stat.S_IMODE(self.auth.identity_path.stat().st_mode), 0o600)
        with patch.object(LocalAuth, '_prepare', side_effect=LocalAuthError('Unavailable')):
            restarted = LocalAuth(self.private / 'unused.swift', self.private)
        self.assertFalse(restarted.status()['touch_id_available'])
        logged_in = restarted.unlock_password(self.password)
        self.assertEqual(restarted.principal(logged_in), {'principal_id': principal, 'method': 'password'})
        self.assertFalse(restarted.valid(self.token))
        with self.assertRaises(LocalAuthError):
            restarted.configure_password(logged_in, None, self.password, remove=True)

    def test_first_setup_requires_session_and_cannot_create_from_password(self):
        with self.assertRaises(LocalAuthError):
            self.auth.configure_password(None, self.password)
        impossible = self.auth._create_session('password')
        with self.assertRaises(LocalAuthError):
            self.auth.configure_password(impossible, self.password, self.password)
        self.assertFalse(self.auth.status()['password_configured'])
        for invalid in ['', 'short', ' ' * 12, None, 'x' * 257, '\ud800' * 12]:
            with self.assertRaises(LocalAuthError):
                self.auth.configure_password(self.token, invalid)

    def test_change_requires_current_password_and_revokes_other_sessions(self):
        self.configure()
        other = self.auth.unlock_password(self.password)
        current = self.auth.unlock_password(self.password)
        with self.assertRaises(LocalAuthError):
            self.auth.configure_password(current, 'Outra senha de teste!', 'wrong')
        self.auth.configure_password(current, 'Outra senha de teste!', self.password)
        self.assertTrue(self.auth.valid(current))
        self.assertFalse(self.auth.valid(other))
        self.assertFalse(self.auth.valid(self.token))
        with self.assertRaises(LocalAuthError):
            self.auth.unlock_password(self.password)
        self.assertTrue(self.auth.valid(self.auth.unlock_password('Outra senha de teste!')))

    def test_attempt_cooldown_and_expiration_are_server_enforced(self):
        self.configure()
        with patch('local_auth.time.monotonic', return_value=1000.0) as clock:
            for _ in range(5):
                with self.assertRaisesRegex(LocalAuthError, 'incorreta'):
                    self.auth.unlock_password('wrong')
            with self.assertRaisesRegex(LocalAuthError, 'Muitas tentativas'):
                self.auth.unlock_password(self.password)
            clock.return_value = 1031.0
            token = self.auth.unlock_password(self.password)
            self.assertTrue(self.auth.valid(token))
            clock.return_value += SESSION_SECONDS
            self.assertFalse(self.auth.valid(token))

    def test_remove_and_biometric_recovery(self):
        self.configure()
        self.auth.configure_password(self.token, 'Nova senha por Touch ID!')
        logged_in = self.auth.unlock_password('Nova senha por Touch ID!')
        with self.assertRaises(LocalAuthError):
            self.auth.configure_password(logged_in, None, 'wrong', remove=True)
        self.auth.configure_password(logged_in, None, 'Nova senha por Touch ID!', remove=True)
        self.assertFalse(self.auth.status()['password_configured'])
        self.auth.lock(logged_in)
        self.assertFalse(self.auth.valid(logged_in))
        with self.assertRaises(LocalAuthError):
            self.auth.unlock_password('Nova senha por Touch ID!')

    def test_invalid_identity_is_never_overwritten(self):
        self.auth.identity_path.write_text('{invalid')
        with self.assertRaises(LocalAuthError):
            LocalAuth(self.private / 'unused.swift', self.private)
        self.assertEqual(self.auth.identity_path.read_text(), '{invalid')


if __name__ == '__main__':
    unittest.main()
