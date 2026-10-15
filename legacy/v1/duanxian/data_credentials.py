"""Private credentials for optional market-data providers.

Unlike ordinary application settings this file contains a secret, is never
returned by an API, and is written with owner-only permissions.  A persisted
"disabled" override is intentional: it lets the settings page disable an old
IWENCAI_API_KEY that still exists in the repository .env.
"""

from __future__ import annotations

import json
import os
import threading

from .util import atomic_write_json

_PATH = os.path.expanduser("~/.duanxian-agents/credentials.json")
_LOCK = threading.RLock()
_SCHEMA = 1


def _read_override() -> dict | None:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if raw.get("schema") != _SCHEMA or not isinstance(raw.get("iwencai"), dict):
        return None
    value = raw["iwencai"]
    if value.get("enabled") is False:
        return {"enabled": False, "key": ""}
    key = value.get("key")
    if value.get("enabled") is True and isinstance(key, str) and key:
        return {"enabled": True, "key": key}
    return None


def _write(value: dict) -> None:
    if not atomic_write_json(_PATH, {"schema": _SCHEMA, "iwencai": value}):
        raise OSError("问财密钥写入失败")
    try:
        os.chmod(_PATH, 0o600)
    except OSError:
        pass


def apply_iwencai_override() -> str:
    """Apply the persisted override after the repository .env is loaded."""
    with _LOCK:
        override = _read_override()
        if override is None:
            return os.environ.get("IWENCAI_API_KEY", "").strip()
        if override["enabled"]:
            os.environ["IWENCAI_API_KEY"] = override["key"]
            return override["key"]
        os.environ.pop("IWENCAI_API_KEY", None)
        return ""


def get_iwencai_key() -> str:
    return apply_iwencai_override()


def save_iwencai_key(api_key: str) -> None:
    key = str(api_key or "").strip()
    if not key or len(key) > 2048 or any(ord(ch) < 32 for ch in key):
        raise ValueError("问财密钥不能为空、不能含控制字符，且长度不能超过 2048")
    with _LOCK:
        _write({"enabled": True, "key": key})
        os.environ["IWENCAI_API_KEY"] = key


def clear_iwencai_key() -> None:
    with _LOCK:
        _write({"enabled": False})
        os.environ.pop("IWENCAI_API_KEY", None)


def iwencai_key_status() -> dict:
    with _LOCK:
        override = _read_override()
        if override is not None:
            return {"configured": bool(override["enabled"] and override["key"]), "source": "settings"}
        return {"configured": bool(os.environ.get("IWENCAI_API_KEY", "").strip()), "source": "environment"}
