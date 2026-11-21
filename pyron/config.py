from __future__ import annotations

import os
from pathlib import Path


def _parse_value(value: str) -> str:
    value = value.strip()
    # Strip an inline comment only when it is preceded by whitespace.
    for marker in (" #", "\t#"):
        index = value.find(marker)
        if index != -1:
            value = value[:index].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def load_env_file(env_path: Path | str | None = None) -> None:
    """Minimal .env loader.

    Existing environment variables always win, so a real environment can
    override the file. Supports ``export KEY=value`` and inline comments.
    """
    if env_path is None:
        candidate = Path.cwd() / ".env"
        if not candidate.is_file():
            candidate = Path(__file__).resolve().parent.parent / ".env"
        env_path = candidate

    path = Path(env_path)
    if not path.is_file():
        return

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _parse_value(value)


# Auto-load on import for backwards compatibility with earlier releases.
load_env_file()
