"""Read the local OpenAI key without exposing it to the browser or Git."""

from __future__ import annotations

import os
from pathlib import Path


class ConfigError(RuntimeError):
    """The local configuration cannot be used safely."""


def get_openai_api_key(env_file: Path) -> str:
    """Prefer a process environment variable; otherwise read OPENAI_API_KEY from .env."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    if not env_file.exists():
        return ""
    if not env_file.is_file():
        raise ConfigError("O caminho .env precisa ser um arquivo.")
    if os.name != "nt" and env_file.stat().st_mode & 0o077:
        raise ConfigError("Proteja o .env com: chmod 600 .env")
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ConfigError("Não foi possível ler o arquivo .env.") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if name.strip() != "OPENAI_API_KEY":
            continue
        if not separator:
            raise ConfigError("Use OPENAI_API_KEY=sua_chave no arquivo .env.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if any(character.isspace() for character in value) or len(value) > 2048:
            raise ConfigError("O valor de OPENAI_API_KEY no .env é inválido.")
        return value
    return ""
