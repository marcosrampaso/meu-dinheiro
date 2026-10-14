import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import FinanceHandler
from local_auth import LocalAuth
import test_api


class PasswordApiTests(unittest.TestCase):
    call_api = test_api.ApiTests.call_api

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = SimpleNamespace(server_port=8765)
        with patch.object(LocalAuth, '_prepare'):
            self.server.local_auth = LocalAuth(Path('unused'), Path(self.directory.name))
        # Existing fake-socket helper uses test-session when authenticated=True.
        self.call = lambda path, method='GET', data=None, headers=None: self.call_api(
            path, method, data, headers=headers, authenticated=False)
        self.touch_token = self.server.local_auth._create_session('touch_id')

    def tearDown(self):
        self.directory.cleanup()

    def test_public_status_protected_configuration_and_origin_guards(self):
        status, state = self.call('/api/v1/local-auth/status')
        self.assertEqual(status, 200)
        self.assertFalse(state['password_configured'])
        self.assertNotIn('principal_id', state)
        self.assertEqual(self.call('/api/v1/local-auth/password', 'POST', {'password':'Test password 42!'})[0], 401)
        self.assertEqual(self.call('/api/v1/local-auth/session')[0], 401)
        self.assertEqual(self.call('/api/v1/local-auth/password-login', 'POST', {'password':'Test password 42!'},
            {'Origin':'https://other.example'})[0], 403)
        self.assertEqual(self.call('/api/v1/local-auth/password', 'POST', {'password':'Test password 42!'},
            {'Origin':'https://other.example', 'X-Local-Session':self.touch_token})[0], 403)
        self.assertEqual(self.call('/api/v1/local-auth/password', 'POST', {'password':'Test password 42!'},
            {'X-Local-Session':self.touch_token})[0], 200)
        self.assertEqual(self.call('/api/v1/local-auth/password-login', 'POST', {'password':'wrong'})[0], 403)
        status, login = self.call('/api/v1/local-auth/password-login', 'POST', {'password':'Test password 42!'})
        self.assertEqual(status, 200)
        self.assertEqual(login['session_seconds'], 300)
        status, principal = self.call('/api/v1/local-auth/session', headers={'X-Local-Session':login['token']})
        self.assertEqual(status, 200)
        self.assertEqual(principal['method'], 'password')
        self.assertEqual(self.call('/api/v1/local-auth/lock', 'POST', {}, {'X-Local-Session':login['token']})[0], 200)
        self.assertEqual(self.call('/api/v1/local-auth/session', headers={'X-Local-Session':login['token']})[0], 401)
