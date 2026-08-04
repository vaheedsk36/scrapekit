"""Backend-cached provider settings.

The browser sends the API key once; it lives here in the server process (and an
optional local file in the user's home) — never persisted in the browser. Falls
back to OPENAI_API_KEY from the environment so the app still works from `.env`.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_STATE = {"provider": None, "model": None, "api_key": None}
_FILE = Path.home() / ".scrapekit" / "settings.json"

# Sensible starting model per provider (users pick from a live-fetched list).
_DEFAULT_MODEL = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-5",
    "xai": "grok-4.5",
}


def _load_file() -> None:
    try:
        data = json.loads(_FILE.read_text())
        for k in ("provider", "model", "api_key"):
            if data.get(k):
                _STATE[k] = data[k]
    except Exception:
        pass


_load_file()


def _persist() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(_STATE))
        os.chmod(_FILE, 0o600)
    except Exception:
        pass


def set_settings(provider: str, model: str, api_key: str) -> None:
    with _LOCK:
        if provider:
            _STATE["provider"] = provider
        _STATE["model"] = model or None
        if api_key:  # only overwrite the key when a new one is supplied
            _STATE["api_key"] = api_key
        _persist()


def get_effective() -> tuple[str, str, "str | None"]:
    """(provider, model, api_key) — settings first, then env fallback."""
    with _LOCK:
        provider = _STATE["provider"] or "openai"
        model = _STATE["model"] or _DEFAULT_MODEL.get(provider)
        key = _STATE["api_key"] or (
            os.environ.get("OPENAI_API_KEY") if provider == "openai" else None
        )
    return provider, model, key


def public_status() -> dict:
    """Safe to send to the browser — never includes the key itself."""
    with _LOCK:
        provider = _STATE["provider"] or ("openai" if os.environ.get("OPENAI_API_KEY") else None)
        has_key = bool(_STATE["api_key"] or os.environ.get("OPENAI_API_KEY"))
        return {
            "provider": provider,
            "model": _STATE["model"] or (_DEFAULT_MODEL.get(provider) if provider else None),
            "has_key": has_key,
            "source": "settings" if _STATE["api_key"] else ("env" if has_key else None),
        }
