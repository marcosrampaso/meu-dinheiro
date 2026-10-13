"""Touch ID gate for the local API. No fingerprint material enters Python."""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path


SESSION_SECONDS = 5 * 60


class LocalAuthError(Exception):
    pass


class LocalAuth:
    def __init__(self, source: Path, private_dir: Path):
        self.source = source
        self.private_dir = private_dir
        self.binary = private_dir / "touchid-helper"
        self._sessions: dict[bytes, float] = {}
        self._lock = threading.Lock()
        self._prompt_lock = threading.Lock()
        self._last_attempt = 0.0
        self._prepare()

    def _prepare(self) -> None:
        if os.uname().sysname != "Darwin":
            raise LocalAuthError("Touch ID requer macOS.")
        self.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.binary.exists() or self.binary.stat().st_mtime < self.source.stat().st_mtime:
            module_cache = self.private_dir / "swift-module-cache"
            module_cache.mkdir(mode=0o700, exist_ok=True)
            environment = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(module_cache),
                           "SWIFT_MODULE_CACHE_PATH": str(module_cache)}
            try:
                result = subprocess.run(
                    ["xcrun", "swiftc", str(self.source), "-o", str(self.binary)],
                    env=environment, capture_output=True, timeout=120, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalAuthError("Não foi possível preparar o Touch ID.") from exc
            if result.returncode:
                self.binary.unlink(missing_ok=True)
                raise LocalAuthError("Não foi possível compilar o acesso por Touch ID.")
            self.binary.chmod(0o700)
        try:
            result = subprocess.run([str(self.binary), "check"], capture_output=True,
                                    timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalAuthError("Não foi possível verificar o Touch ID.") from exc
        if result.returncode:
            raise LocalAuthError("Touch ID indisponível ou sem digital cadastrada. O app ficará bloqueado.")

    def unlock(self) -> str:
        if not self._prompt_lock.acquire(blocking=False):
            raise LocalAuthError("Já existe uma solicitação de Touch ID em andamento.")
        try:
            now = time.monotonic()
            if now - self._last_attempt < 2:
                raise LocalAuthError("Aguarde um instante antes de tentar novamente.")
            self._last_attempt = now
            try:
                result = subprocess.run([str(self.binary), "authenticate"],
                                        capture_output=True, timeout=100, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalAuthError("A solicitação de Touch ID não foi concluída.") from exc
            if result.returncode:
                raise LocalAuthError("Touch ID não aprovado. Nenhum dado foi liberado.")
            token = secrets.token_urlsafe(32)
            with self._lock:
                now = time.monotonic()
                self._sessions = {key: expiry for key, expiry in self._sessions.items()
                                  if expiry > now}
                self._sessions[hashlib.sha256(token.encode()).digest()] = now + SESSION_SECONDS
            return token
        finally:
            self._prompt_lock.release()

    def valid(self, token: str | None) -> bool:
        if not isinstance(token, str) or len(token) > 100:
            return False
        digest = hashlib.sha256(token.encode()).digest()
        with self._lock:
            expiry = self._sessions.get(digest, 0)
            if expiry <= time.monotonic():
                self._sessions.pop(digest, None)
                return False
            return True

    def lock(self, token: str | None) -> None:
        if isinstance(token, str) and len(token) <= 100:
            with self._lock:
                self._sessions.pop(hashlib.sha256(token.encode()).digest(), None)
