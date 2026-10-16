"""Local access gate. Password hashes stay on disk; session tokens stay in memory."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

SESSION_SECONDS = 5 * 60
SCRYPT_N = 32768


class LocalAuthError(Exception):
    pass


class LocalAuth:
    def __init__(self, source: Path, private_dir: Path):
        self.source = source
        self.private_dir = private_dir
        self.binary = private_dir / "touchid-helper"
        self.identity_path = private_dir / "local-identity.json"
        self.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._sessions: dict[bytes, dict] = {}
        self._lock = threading.Lock()
        self._prompt_lock = threading.Lock()
        self._password_lock = threading.Lock()
        self._last_attempt = 0.0
        self._password_failures = 0
        self._password_retry_at = 0.0
        self._identity = self._load_identity()
        self.touch_id_available = False
        try:
            self._prepare()
            self.touch_id_available = True
        except LocalAuthError:
            # Keep the login page available, including previously configured password access.
            pass

    def _load_identity(self) -> dict:
        if self.identity_path.exists():
            try:
                identity = json.loads(self.identity_path.read_text())
                if not isinstance(identity.get("principal_id"), str) or not identity["principal_id"]:
                    raise ValueError("Invalid principal")
                credential = identity.get("password")
                if credential is not None:
                    if credential.get("algorithm") != "scrypt-v1":
                        raise ValueError("Invalid credential algorithm")
                    if len(bytes.fromhex(credential["salt"])) != 32 or len(bytes.fromhex(credential["hash"])) != 64:
                        raise ValueError("Invalid credential")
                self.identity_path.chmod(0o600)
                return identity
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise LocalAuthError("O arquivo de identidade local está inválido. Restaure seu backup; ele não foi sobrescrito.") from exc
        identity = {"version": 1, "principal_id": secrets.token_urlsafe(24), "password": None}
        self._write_identity(identity)
        return identity

    def _write_identity(self, identity: dict) -> None:
        # Exclusive temporary file, then atomic replacement. Neither contains the clear password.
        temporary = self.private_dir / (".local-identity-" + secrets.token_hex(12))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                json.dump(identity, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.identity_path)
            self.identity_path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _prepare(self) -> None:
        if platform.system() != "Darwin":
            raise LocalAuthError("Touch ID requer macOS.")
        bundled = self.source.with_name('touchid-helper')
        if not self.binary.exists() and bundled.is_file() and bundled.resolve() != self.binary.resolve():
            shutil.copyfile(bundled,self.binary)
            self.binary.chmod(0o700)
        if not self.binary.exists() or self.binary.stat().st_mtime < self.source.stat().st_mtime:
            module_cache = self.private_dir / "swift-module-cache"
            module_cache.mkdir(mode=0o700, exist_ok=True)
            environment = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(module_cache),
                           "SWIFT_MODULE_CACHE_PATH": str(module_cache)}
            try:
                result = subprocess.run(["xcrun", "swiftc", str(self.source), "-o", str(self.binary)],
                                        env=environment, capture_output=True, timeout=120, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalAuthError("Não foi possível preparar o Touch ID.") from exc
            if result.returncode:
                self.binary.unlink(missing_ok=True)
                raise LocalAuthError("Não foi possível compilar o acesso por Touch ID.")
            self.binary.chmod(0o700)
        try:
            result = subprocess.run([str(self.binary), "check"], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalAuthError("Não foi possível verificar o Touch ID.") from exc
        if result.returncode:
            raise LocalAuthError("Touch ID indisponível ou sem digital cadastrada.")

    def status(self) -> dict:
        return {"touch_id_available": self.touch_id_available,
                "password_configured": self._identity.get("password") is not None,
                "session_seconds": SESSION_SECONDS}

    def _now(self) -> float:
        return time.monotonic()

    def _create_session(self, method: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._now()
            self._sessions = {key: session for key, session in self._sessions.items()
                              if session["expires_at"] > now}
            self._sessions[hashlib.sha256(token.encode()).digest()] = {
                "expires_at": now + SESSION_SECONDS, "method": method,
                "principal_id": getattr(self, "_identity", {}).get("principal_id", "local-owner")}
        return token

    def unlock(self) -> str:
        if not getattr(self, "touch_id_available", True):
            raise LocalAuthError("Touch ID indisponível. Use a senha local se já estiver cadastrada.")
        if not self._prompt_lock.acquire(blocking=False):
            raise LocalAuthError("Já existe uma solicitação de Touch ID em andamento.")
        try:
            now = time.monotonic()
            if now - self._last_attempt < 2:
                raise LocalAuthError("Aguarde um instante antes de tentar novamente.")
            self._last_attempt = now
            try:
                result = subprocess.run([str(self.binary), "authenticate"], capture_output=True,
                                        timeout=100, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalAuthError("A solicitação de Touch ID não foi concluída.") from exc
            if result.returncode:
                raise LocalAuthError("Touch ID não aprovado. Nenhum dado foi liberado.")
            return self._create_session("touch_id")
        finally:
            self._prompt_lock.release()

    @staticmethod
    def _password_text(value) -> str:
        try:
            valid = isinstance(value, str) and 12 <= len(value) <= 256 and len(value.encode("utf-8")) <= 1024
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise LocalAuthError("Use uma senha de 12 a 256 caracteres.")
        if not value.strip():
            raise LocalAuthError("A senha não pode conter somente espaços.")
        return value

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1,
                              maxmem=64 * 1024 * 1024, dklen=64)

    def _check_password(self, password) -> None:
        # Caller holds the password lock, preventing concurrent guesses bypassing the cooldown.
        now = time.monotonic()
        if now < self._password_retry_at:
            raise LocalAuthError("Muitas tentativas. Aguarde antes de tentar novamente.")
        credential = self._identity.get("password")
        try:
            candidate = password if isinstance(password, str) and len(password) <= 256 and len(password.encode("utf-8")) <= 1024 else ""
        except UnicodeEncodeError:
            candidate = ""
        salt = bytes.fromhex(credential["salt"]) if credential else bytes(32)
        expected = bytes.fromhex(credential["hash"]) if credential else bytes(64)
        matches = hmac.compare_digest(self._hash_password(candidate, salt), expected)
        if not credential or not matches:
            self._password_failures += 1
            if self._password_failures >= 5:
                self._password_retry_at = now + min(300, 30 * 2 ** min(4, self._password_failures - 5))
            raise LocalAuthError("Senha incorreta ou acesso por senha ainda não configurado.")
        self._password_failures = 0
        self._password_retry_at = 0

    def unlock_password(self, password) -> str:
        with self._password_lock:
            self._check_password(password)
            return self._create_session("password")

    def principal(self, token: str | None) -> dict | None:
        if not isinstance(token, str) or len(token) > 100:
            return None
        digest = hashlib.sha256(token.encode()).digest()
        with self._lock:
            session = self._sessions.get(digest)
            if not session or session["expires_at"] <= self._now():
                self._sessions.pop(digest, None)
                return None
            return {"principal_id": session["principal_id"], "method": session["method"]}

    def valid(self, token: str | None) -> bool:
        return self.principal(token) is not None

    def session_info(self, token: str | None) -> dict | None:
        """Read the idle deadline without renewing it (including reloads and polls)."""
        if not isinstance(token, str) or len(token) > 100:
            return None
        digest = hashlib.sha256(token.encode()).digest()
        with self._lock:
            session = self._sessions.get(digest)
            now = self._now()
            if not session or session["expires_at"] <= now:
                self._sessions.pop(digest, None)
                return None
            return {"principal_id": session["principal_id"], "method": session["method"],
                    "remaining_seconds": session["expires_at"] - now, "session_seconds": SESSION_SECONDS}

    def record_activity(self, token: str | None, idle_for_ms: int = 0) -> dict | None:
        """Renew only a still-valid session after an explicit activity notification."""
        if isinstance(idle_for_ms, bool) or not isinstance(idle_for_ms, int) or not 0 <= idle_for_ms < SESSION_SECONDS * 1000:
            raise LocalAuthError("Atividade inválida ou antiga demais para renovar a sessão.")
        if not isinstance(token, str) or len(token) > 100:
            return None
        digest = hashlib.sha256(token.encode()).digest()
        with self._lock:
            now = self._now()
            session = self._sessions.get(digest)
            if not session or session["expires_at"] <= now:
                self._sessions.pop(digest, None)
                return None
            # A throttled last event may arrive later. Its age must not extend inactivity.
            session["expires_at"] = max(session["expires_at"], now + SESSION_SECONDS - idle_for_ms / 1000)
            return {"session_seconds": SESSION_SECONDS, "remaining_seconds": session["expires_at"] - now}

    def configure_password(self, token: str | None, password, current_password=None, remove=False) -> dict:
        session = self.principal(token)
        if session is None:
            raise LocalAuthError("Desbloqueie o aplicativo antes de configurar a senha.")
        new_password = None if remove else self._password_text(password)
        with self._password_lock:
            if session["method"] != "touch_id":
                self._check_password(current_password)
            if remove and not self.touch_id_available:
                raise LocalAuthError("Não é possível remover a senha enquanto o Touch ID está indisponível.")
            credential = None
            if new_password is not None:
                salt = secrets.token_bytes(32)
                credential = {"algorithm": "scrypt-v1", "salt": salt.hex(),
                              "hash": self._hash_password(new_password, salt).hex()}
            identity = {**self._identity, "password": credential}
            self._write_identity(identity)
            self._identity = identity
            self._password_failures = 0
            self._password_retry_at = 0
            # Invalidate other logins after a credential change, preserving this authorized session.
            digest = hashlib.sha256(token.encode()).digest()
            with self._lock:
                self._sessions = {key: value for key, value in self._sessions.items() if key == digest}
        return self.status()

    def lock(self, token: str | None) -> None:
        if isinstance(token, str) and len(token) <= 100:
            with self._lock:
                self._sessions.pop(hashlib.sha256(token.encode()).digest(), None)
