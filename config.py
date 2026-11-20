"""Single place that loads model settings from the local .env file.

The three variables are read only through this module so the baseline and the
multi-agent team always see identical settings. Values are never printed.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
REQUIRED = ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")

_loaded = False


def load_env() -> None:
    """Load .env once without overriding variables already set by the shell."""
    global _loaded
    if _loaded:
        return
    load_dotenv(ENV_PATH, override=False)
    _loaded = True


def model_config() -> dict:
    """Return the settings plus a missing-name list, without exposing values."""
    load_env()
    values = {name: (os.getenv(name) or "").strip() for name in REQUIRED}
    missing = [name for name in REQUIRED if not values[name]]
    return {
        "api_key": values["MODEL_API_KEY"],
        "base_url": values["MODEL_BASE_URL"],
        "model": values["MODEL_NAME"],
        "missing": missing,
        "configured": not missing,
    }


def require_model_config() -> dict:
    """Return settings or raise a clear, value-free configuration error."""
    config = model_config()
    if config["missing"]:
        raise RuntimeError(
            "Set " + ", ".join(config["missing"]) + " in .env (or the shell) before evaluation"
        )
    return config
