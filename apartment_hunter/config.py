"""Configuration + environment loading.

The default config (``config.json``) is pure JSON so the demo needs no
dependencies. A ``.yaml`` config is also supported for real use (requires
PyYAML). A tiny built-in ``.env`` reader means we don't depend on
python-dotenv either.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Populate os.environ from a local .env file (does not overwrite existing)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    _load_env()
    cfg_path = Path(path) if path else PROJECT_ROOT / "config.json"
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    text = cfg_path.read_text()
    if cfg_path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # optional dependency
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "A .yaml config needs PyYAML — `pip install pyyaml`, "
                "or use the default config.json."
            ) from exc
        return yaml.safe_load(text)
    return json.loads(text)
