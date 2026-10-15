"""Local Sign in with ChatGPT session for ChatGPT plan usage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import jwt


AUTHORIZATION_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
MODELS_URL = "https://api.openai.com/v1/models"
RESOURCE = "https://api.openai.com/v1"
ISSUER = "https://auth.openai.com"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"


class ChatGPTAuthError(RuntimeError):
    """The ChatGPT connection could not be completed safely."""

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


def _post_form(url: str, fields: dict[str, str]) -> dict:
    request = Request(url, data=urlencode(fields).encode("ascii"),
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("Resposta inválida")
            return payload
    except HTTPError as exc:
        try:
            error_body = json.loads(exc.read(8192))
            error = error_body.get("error")
            code = error.get("code") if isinstance(error, dict) else error
        except (ValueError, AttributeError, OSError):
            code = None
        raise ChatGPTAuthError(f"O ChatGPT recusou a autenticação (HTTP {exc.code}).", code=code) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ChatGPTAuthError("Não foi possível concluir a autenticação com o ChatGPT.") from exc


class ChatGPTAuth:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.pending: dict[str, dict] = {}
        self.jwks = jwt.PyJWKClient(JWKS_URL)
        self.data = self._load()
        self.last_error: str | None = None

    def _load(self) -> dict:
        if not self.path.exists():
            return {"host_id": f"urn:uuid:{uuid.uuid4()}", "accounts": {}, "active": None}
        if self.path.is_symlink() or not self.path.is_file():
            raise ChatGPTAuthError("O arquivo de conexão do ChatGPT é inválido.")
        if os.name != "nt" and self.path.stat().st_mode & 0o077:
            raise ChatGPTAuthError("Proteja data/chatgpt-auth.json com chmod 600.")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data["host_id"], str) or not isinstance(data["accounts"], dict):
                raise ValueError("Formato inválido")
            return data
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise ChatGPTAuthError("Não foi possível ler a conexão salva do ChatGPT.") from exc

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".chatgpt-auth-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def status(self) -> dict:
        with self.lock:
            active = self.data["accounts"].get(self.data.get("active"))
            return {
                "connected": bool(active and active.get("access_token") and
                                  DIRECT_SCOPE in active.get("scopes", [])),
                "email": active.get("email") if active else None,
                "model": active.get("model") if active else None,
                "last_error": self.last_error,
                "accounts": [{"client_id": client_id, "email": account.get("email"),
                              "active": client_id == self.data.get("active")}
                             for client_id, account in self.data["accounts"].items()],
            }

    def start(self, port: int, *, new_account: bool = False, client_id: str | None = None) -> str:
        with self.lock:
            self.last_error = None
            if new_account and client_id:
                raise ChatGPTAuthError("Escolha uma conta salva ou adicione uma nova.")
            if client_id and client_id not in self.data["accounts"]:
                raise ChatGPTAuthError("Conta ChatGPT desconhecida.")
            previous_id = None if new_account else (client_id or self.data.get("active"))
            previous = self.data["accounts"].get(previous_id)
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
            self.pending = {state: {"nonce": nonce, "verifier": verifier,
                                    "client_id": previous_id if previous else None,
                                    "redirect_uri": redirect_uri, "created_at": time.time()}}
            self._save()  # The host ID must survive a restart before authorization.
            parameters = {
                "client_id": previous_id if previous else "dynamic_agent_client",
                "ext_agent_host_id": self.data["host_id"],
                "response_type": "code", "redirect_uri": redirect_uri,
                "scope": SCOPES, "resource": RESOURCE, "state": state, "nonce": nonce,
                "code_challenge_method": "S256",
                "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
            }
            if not previous:
                parameters["agent_name_hint"] = "Meu Dinheiro"
            elif DIRECT_SCOPE not in previous.get("scopes", []):
                parameters["prompt"] = "consent"
            return AUTHORIZATION_URL + "?" + urlencode(parameters)

    def complete(self, query: dict[str, list[str]]) -> None:
        state = query.get("state", [""])[0]
        with self.lock:
            attempt = self.pending.pop(state, None)
        if not attempt or time.time() - attempt["created_at"] > 600:
            raise ChatGPTAuthError("Login expirado ou inválido. Tente conectar novamente.")
        if query.get("error"):
            raise ChatGPTAuthError("A conexão com o ChatGPT foi cancelada.")
        code = query.get("code", [""])[0]
        returned_id = query.get("client_id", [None])[0]
        expected_id = attempt["client_id"]
        if expected_id:
            if returned_id and returned_id != expected_id:
                raise ChatGPTAuthError("O ChatGPT retornou outra aplicação. Tente novamente.")
            client_id = expected_id
        else:
            if not returned_id or returned_id == "dynamic_agent_client":
                raise ChatGPTAuthError("O ChatGPT não retornou o identificador da aplicação.")
            client_id = returned_id
        if not code:
            raise ChatGPTAuthError("O ChatGPT não retornou um código de autorização.")
        tokens = _post_form(TOKEN_URL, {
            "grant_type": "authorization_code", "client_id": client_id,
            "code": code, "code_verifier": attempt["verifier"],
            "redirect_uri": attempt["redirect_uri"], "resource": RESOURCE,
        })
        identity = self._verify_id_token(tokens.get("id_token"), client_id, attempt["nonce"])
        scopes = tokens.get("scope", "").split()
        if not tokens.get("access_token") or not tokens.get("refresh_token"):
            raise ChatGPTAuthError("O ChatGPT não retornou uma sessão renovável.")
        with self.lock:
            old = self.data["accounts"].get(client_id)
            if old and old.get("subject") != identity["sub"]:
                raise ChatGPTAuthError("A conta retornada difere da conta conectada antes.")
            self.data["accounts"][client_id] = {
                "subject": identity["sub"], "email": identity.get("email"),
                "client_id": client_id, "id_token": tokens["id_token"],
                "access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"],
                "scopes": scopes, "expires_at": time.time() + int(tokens.get("expires_in", 3600)),
                "model": old.get("model") if old else None,
            }
            self.data["active"] = client_id
            self.last_error = (None if DIRECT_SCOPE in scopes else
                               "Você entrou no ChatGPT, mas não autorizou o uso do plano. Conecte novamente para permitir.")
            self._save()

    def _verify_id_token(self, id_token: str | None, client_id: str, nonce: str) -> dict:
        if not id_token:
            raise ChatGPTAuthError("O ChatGPT não retornou a identidade da conta.")
        try:
            key = self.jwks.get_signing_key_from_jwt(id_token)
            identity = jwt.decode(id_token, key.key, algorithms=["RS256"],
                                  issuer=ISSUER, audience=client_id, leeway=5,
                                  options={"require": ["sub", "exp", "iat", "nonce"]})
        except (jwt.PyJWTError, ValueError, TypeError, URLError) as exc:
            raise ChatGPTAuthError("Não foi possível validar a identidade do ChatGPT.") from exc
        if (not isinstance(identity.get("sub"), str) or not identity["sub"]
                or not isinstance(identity.get("nonce"), str)
                or not hmac.compare_digest(identity["nonce"], nonce)):
            raise ChatGPTAuthError("A identidade do ChatGPT não corresponde ao login iniciado.")
        return identity

    def _access_token(self) -> tuple[str, dict]:
        with self.lock:
            account = self.data["accounts"].get(self.data.get("active"))
            if not account or DIRECT_SCOPE not in account.get("scopes", []):
                raise ChatGPTAuthError("Conecte o ChatGPT e autorize o uso do seu plano primeiro.")
            if time.time() >= account.get("expires_at", 0) - 300:
                try:
                    tokens = _post_form(TOKEN_URL, {
                        "grant_type": "refresh_token", "client_id": account["client_id"],
                        "refresh_token": account["refresh_token"], "resource": RESOURCE,
                    })
                except ChatGPTAuthError as exc:
                    if exc.code in {"invalid_grant", "invalid_refresh_token", "token_expired",
                                    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}:
                        for field in ("access_token", "refresh_token", "id_token"):
                            account.pop(field, None)
                        account["scopes"] = []
                        self._save()
                        raise ChatGPTAuthError("A sessão do ChatGPT expirou. Conecte novamente.") from exc
                    raise ChatGPTAuthError("Não foi possível renovar a sessão do ChatGPT. Tente novamente em instantes.") from exc
                if not tokens.get("access_token") or not tokens.get("refresh_token"):
                    raise ChatGPTAuthError("Não foi possível renovar a sessão do ChatGPT.")
                account["access_token"] = tokens["access_token"]
                account["refresh_token"] = tokens["refresh_token"]
                account["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
                if tokens.get("scope"):
                    account["scopes"] = tokens["scope"].split()
                if DIRECT_SCOPE not in account["scopes"]:
                    self._save()
                    raise ChatGPTAuthError("O uso do plano ChatGPT não está autorizado. Conecte novamente.")
                self._save()
            return account["access_token"], account

    def inference_credentials(self) -> tuple[str, str]:
        with self.lock:
            token, account = self._access_token()
            if account.get("model"):
                return token, account["model"]
            request = Request(MODELS_URL, headers={"Authorization": f"Bearer {token}"})
            try:
                with urlopen(request, timeout=20) as response:
                    catalog = json.load(response)
                available = [item["slug"] for item in catalog.get("models", [])
                             if isinstance(item, dict) and item.get("visibility") == "list"
                             and isinstance(item.get("slug"), str)]
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as exc:
                raise ChatGPTAuthError("Não foi possível consultar os modelos do seu plano ChatGPT.") from exc
            if not available:
                raise ChatGPTAuthError("Nenhum modelo disponível para esta conta ChatGPT.")
            model = next((name for name in ("gpt-6-luna", "gpt-6.1-sol") if name in available), available[0])
            account["model"] = model
            self._save()
            return token, model

    def luna_credentials(self) -> tuple[str, str]:
        """Use Luna explicitly for market research, with no silent model fallback."""
        with self.lock:
            token, account = self._access_token()
            if account.get("model") == "gpt-6-luna":
                return token, "gpt-6-luna"
            request = Request(MODELS_URL, headers={"Authorization": f"Bearer {token}"})
            try:
                with urlopen(request, timeout=20) as response:
                    catalog = json.load(response)
                available = {item["slug"] for item in catalog.get("models", [])
                             if isinstance(item, dict) and item.get("visibility") == "list"
                             and isinstance(item.get("slug"), str)}
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as exc:
                raise ChatGPTAuthError("Não foi possível consultar os modelos do seu plano ChatGPT.") from exc
            if "gpt-6-luna" not in available:
                raise ChatGPTAuthError("GPT-6 Luna não está disponível nesta conta ChatGPT.")
            account["model"] = "gpt-6-luna"
            self._save()
            return token, "gpt-6-luna"

    def research_credentials(self, model='gpt-6.1-sol') -> tuple[str, str]:
        """Check the selected research model without changing categorization's model."""
        if model not in ('gpt-6.1-sol','gpt-6-luna'):
            raise ChatGPTAuthError('Escolha GPT-6.1 Sol ou GPT-6 Luna para pesquisar.')
        with self.lock:
            token, account = self._access_token()
            request = Request(MODELS_URL, headers={'Authorization':f'Bearer {token}'})
            try:
                with urlopen(request, timeout=20) as response:
                    catalog = json.load(response)
                available = {item['slug'] for item in catalog.get('models',[])
                             if isinstance(item,dict) and item.get('visibility')=='list' and isinstance(item.get('slug'),str)}
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError,KeyError) as exc:
                raise ChatGPTAuthError('Não foi possível consultar os modelos do seu plano ChatGPT.') from exc
            if model not in available:
                raise ChatGPTAuthError(f'{model} não está disponível nesta conta. Escolha outro modelo de pesquisa.')
            return token, model

    @staticmethod
    def _revoke(refresh_token: str, client_id: str) -> None:
        with urlopen(DISCOVERY_URL, timeout=20) as response:
            configuration = json.load(response)
        endpoint = configuration.get("revocation_endpoint", "")
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or parsed.hostname != "auth.openai.com":
            raise ChatGPTAuthError("O endpoint de revogação do ChatGPT é inválido.")
        request = Request(endpoint, data=urlencode({
            "token": refresh_token, "token_type_hint": "refresh_token", "client_id": client_id,
        }).encode("ascii"), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urlopen(request, timeout=20):
            pass

    def disconnect(self) -> None:
        with self.lock:
            active_id = self.data.get("active")
            account = self.data["accounts"].get(active_id)
            if not account:
                return
            refresh_token = account.get("refresh_token")
            if refresh_token:
                try:
                    self._revoke(refresh_token, active_id)
                    self.last_error = None
                except (ChatGPTAuthError, HTTPError, URLError, TimeoutError, OSError, ValueError):
                    self.last_error = ("Desconectado neste computador. A revogação remota não foi confirmada; "
                                       "confira o acesso nas configurações do ChatGPT.")
            for field in ("access_token", "refresh_token", "id_token"):
                account.pop(field, None)
            account["scopes"] = []
            account["model"] = None
            self._save()
