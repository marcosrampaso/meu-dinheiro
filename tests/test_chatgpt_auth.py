import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from chatgpt_auth import ChatGPTAuth, ChatGPTAuthError, DIRECT_SCOPE


class JsonResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.body


class ChatGPTAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "data" / "chatgpt-auth.json"
        self.auth = ChatGPTAuth(self.path)
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def tearDown(self):
        self.directory.cleanup()

    def _start(self):
        url = self.auth.start(8767)
        return parse_qs(urlsplit(url).query)

    def _token_response(self, nonce, scopes=None):
        claims = {"iss": "https://auth.openai.com", "aud": "oaiapp_test", "sub": "user-123",
                  "email": "teste@exemplo.com", "nonce": nonce,
                  "iat": int(time.time()), "exp": int(time.time()) + 3600}
        return {"id_token": jwt.encode(claims, self.private_key, algorithm="RS256"),
                "access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600,
                "scope": " ".join(scopes if scopes is not None else [DIRECT_SCOPE, "offline_access"])}

    def _complete(self, scopes=None):
        parameters = self._start()
        nonce = parameters["nonce"][0]
        tokens = self._token_response(nonce, scopes)
        callback = {"state": parameters["state"], "code": ["auth-code"],
                    "client_id": ["oaiapp_test"]}
        with patch("chatgpt_auth._post_form", return_value=tokens) as exchange:
            with patch.object(self.auth.jwks, "get_signing_key_from_jwt",
                              return_value=SimpleNamespace(key=self.private_key.public_key())):
                self.auth.complete(callback)
        return parameters, exchange.call_args.args

    def test_pkce_callback_identity_and_private_storage(self):
        parameters, exchange_args = self._complete()
        self.assertEqual(parameters["client_id"], ["dynamic_agent_client"])
        self.assertEqual(parameters["scope"][0].split(),
                         ["openid", "profile", "email", "offline_access", "resource.invoke", DIRECT_SCOPE])
        self.assertEqual(parameters["redirect_uri"], ["http://127.0.0.1:8767/auth/callback"])
        self.assertEqual(exchange_args[1]["client_id"], "oaiapp_test")
        self.assertTrue(self.auth.status()["connected"])
        self.assertNotIn("access_token", self.auth.status())
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        saved_host = self.auth.data["host_id"]
        self.assertEqual(ChatGPTAuth(self.path).data["host_id"], saved_host)
        self.assertEqual(parse_qs(urlsplit(self.auth.start(8767)).query)["client_id"], ["oaiapp_test"])

    def test_invalid_nonce_or_denied_scope_never_enables_inference(self):
        parameters = self._start()
        tokens = self._token_response("another-nonce")
        with patch("chatgpt_auth._post_form", return_value=tokens):
            with patch.object(self.auth.jwks, "get_signing_key_from_jwt",
                              return_value=SimpleNamespace(key=self.private_key.public_key())):
                with self.assertRaises(ChatGPTAuthError):
                    self.auth.complete({"state": parameters["state"], "code": ["code"],
                                        "client_id": ["oaiapp_test"]})
        self.assertFalse(self.auth.status()["connected"])
        self._complete(scopes=["openid", "offline_access"])
        self.assertFalse(self.auth.status()["connected"])
        with self.assertRaises(ChatGPTAuthError):
            self.auth.inference_credentials()
        self.assertEqual(parse_qs(urlsplit(self.auth.start(8767)).query)["prompt"], ["consent"])

    def test_refresh_rotates_token_and_uses_listed_model(self):
        self._complete()
        self.auth.data["accounts"]["oaiapp_test"]["expires_at"] = time.time() + 20
        rotated = {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600,
                   "scope": DIRECT_SCOPE}
        catalog = {"models": [{"slug": "gpt-6.1-sol", "visibility": "list"},
                              {"slug": "hidden", "visibility": "hidden"}]}
        with patch("chatgpt_auth._post_form", return_value=rotated):
            with patch("chatgpt_auth.urlopen", return_value=JsonResponse(catalog)):
                token, model = self.auth.inference_credentials()
        self.assertEqual((token, model), ("access-2", "gpt-6.1-sol"))
        self.assertEqual(self.auth.data["accounts"]["oaiapp_test"]["refresh_token"], "refresh-2")

    def test_disconnect_clears_local_tokens(self):
        self._complete()
        with patch.object(self.auth, "_revoke") as revoke:
            self.auth.disconnect()
        revoke.assert_called_once_with("refresh-1", "oaiapp_test")
        self.assertFalse(self.auth.status()["connected"])
        saved = self.path.read_text()
        self.assertNotIn("access-1", saved)
        self.assertNotIn("refresh-1", saved)

    def test_research_model_is_explicit_available_and_does_not_change_categorization(self):
        self._complete()
        account=self.auth.data['accounts']['oaiapp_test']
        account['model']='gpt-6-luna'
        catalog={'models':[{'slug':'gpt-6.1-sol','visibility':'list'}]}
        with patch('chatgpt_auth.urlopen',return_value=JsonResponse(catalog)):
            self.assertEqual(self.auth.research_credentials(),('access-1','gpt-6.1-sol'))
            self.assertEqual(account['model'],'gpt-6-luna')
            with self.assertRaises(ChatGPTAuthError): self.auth.research_credentials('gpt-6-luna')
        with self.assertRaises(ChatGPTAuthError): self.auth.research_credentials('unknown')


if __name__ == "__main__":
    unittest.main()
