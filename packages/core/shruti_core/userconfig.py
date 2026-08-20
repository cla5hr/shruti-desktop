"""In-app settings for the desktop exe: a JSON file overlaid onto env config.

The web Settings tab writes this file through /api/settings. Values are exported
as env vars (which outrank .env in pydantic-settings) and the settings cache is
cleared, so every later get_settings() call — API and worker alike, same process —
sees the new values without a restart. Only whitelisted keys ever cross this
boundary; everything else stays env/.env-only.
"""

import json
import os
from pathlib import Path

from shruti_core.settings import get_settings

# What the Settings tab may read and write. Booleans and ints round-trip through
# strings the same way .env values do.
EDITABLE_KEYS = (
    "asr_model",
    "asr_device",
    "asr_compute_type",
    "diarize_num_speakers",
    "llm_mode",
    "llm_base_url",
    "llm_model",
    "llm_api_key",
    "llm_max_ctx",
)


def settings_file() -> Path | None:
    raw = get_settings().settings_file
    return Path(raw) if raw else None


def load_overlay() -> dict:
    path = settings_file()
    if path is None or not path.is_file():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in stored.items() if k in EDITABLE_KEYS}


def _export_env(values: dict) -> None:
    for key, value in values.items():
        os.environ[key.upper()] = str(value)
    get_settings.cache_clear()


def apply_overlay() -> dict:
    """Called once at desktop startup, after SETTINGS_FILE is set in the env."""
    values = load_overlay()
    if values:
        _export_env(values)
    return values


def save_overlay(changes: dict) -> dict:
    """Merge whitelisted changes into the file and apply them to the process."""
    path = settings_file()
    if path is None:
        raise RuntimeError("no settings file configured (not running in desktop mode?)")
    values = load_overlay()
    values.update({k: v for k, v in changes.items() if k in EDITABLE_KEYS})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=2), encoding="utf-8")
    tmp.replace(path)
    _export_env(values)
    return values
