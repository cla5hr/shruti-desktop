"""In-app Settings: read/change models, engines, and keys from the UI.

GET is always available (the UI also uses it to learn which features exist in this
build). PUT only works when a settings file is configured — i.e. the desktop app.
"""

import importlib.util
import time
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shruti_api.deps import get_db
from shruti_api.serializers import job_public
from shruti_core import jobs
from shruti_core.cuda import cuda_usable
from shruti_core.settings import get_settings
from shruti_core.userconfig import EDITABLE_KEYS, save_overlay

router = APIRouter(prefix="/api", tags=["settings"])

Db = Annotated[Session, Depends(get_db)]

# Curated faster-whisper model choices for the picker. All auto-download from
# Hugging Face on first use and are cached locally after that.
ASR_CHOICES = [
    {"id": "tiny", "label": "Whisper Tiny", "detail": "Fastest, lowest accuracy", "sizeMB": 75},
    {"id": "base", "label": "Whisper Base", "detail": "Quick and light", "sizeMB": 140},
    {"id": "small", "label": "Whisper Small", "detail": "Good balance for CPU", "sizeMB": 460},
    {
        "id": "medium",
        "label": "Whisper Medium",
        "detail": "High accuracy, slow on CPU",
        "sizeMB": 1500,
    },
    {
        "id": "distil-large-v3",
        "label": "Distil Large v3",
        "detail": "Near-best accuracy, English-focused",
        "sizeMB": 1500,
    },
    {
        "id": "large-v3",
        "label": "Whisper Large v3",
        "detail": "Best accuracy, slowest",
        "sizeMB": 3000,
    },
]

_INT_KEYS = {"llm_max_ctx", "diarize_num_speakers"}


def _is_ollama(base_url: str) -> bool:
    return ":11434" in base_url


_ollama_cache: tuple[float, str, list[str] | None] = (0.0, "", None)


def _ollama_models(
    base_url: str, timeout: float = 0.5, use_cache: bool = True
) -> list[str] | None:
    """Installed Ollama models, or None when the server isn't reachable.

    Short timeout + 5s cache: when Ollama is NOT running, a localhost connect on
    Windows hangs until the timeout rather than refusing — with the old 2s timeout
    every Settings load took 2+ seconds. Save-time validation passes a longer
    timeout and bypasses the cache: rejecting a save because a remote server
    needed 0.6s (or because a failed page-load probe was cached) is worse than a
    save taking a few seconds."""
    global _ollama_cache
    ts, cached_url, cached = _ollama_cache
    if use_cache and cached_url == base_url and time.monotonic() - ts < 5:
        return cached
    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root
    try:
        r = httpx.get(f"{root}/api/tags", timeout=timeout)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        models = None
    _ollama_cache = (time.monotonic(), base_url, models)
    return models


def _diarization_available() -> bool:
    """Speaker separation needs sherpa-onnx (bundled in the exe; pip-installed in dev)."""
    return importlib.util.find_spec("sherpa_onnx") is not None


def _cuda_usable() -> bool:
    """GPU present AND its CUDA libraries loadable (see shruti_core.cuda) — gates both
    the save-time validation and whether the UI shows the GPU option at all."""
    return cuda_usable()


def _asr_installed(model_id: str) -> bool | None:
    """True/False when detectable; None when faster-whisper isn't importable here."""
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        return None
    try:
        download_model(model_id, local_files_only=True)
        return True
    except Exception:
        return False


def _current_values() -> dict:
    s = get_settings()
    return {k: getattr(s, k) for k in EDITABLE_KEYS}


def _public() -> dict:
    s = get_settings()
    ollama_models = _ollama_models(s.llm_base_url) if _is_ollama(s.llm_base_url) else None
    # embedding-only models can't chat — offering them in the minutes picker just
    # sets people up for a failed summary (validation still accepts them if typed)
    pickable = [m for m in (ollama_models or []) if "embed" not in m.lower()]
    return {
        "values": _current_values(),
        "meta": {
            "editable": bool(s.settings_file),
            "diarization_available": _diarization_available(),
            "cuda_available": _cuda_usable(),
            "asr_models": [{**m, "installed": _asr_installed(m["id"])} for m in ASR_CHOICES],
            "ollama": {"running": ollama_models is not None, "models": pickable},
        },
    }


@router.get("/settings")
def get_app_settings() -> dict:
    return _public()


class SettingsBody(BaseModel):
    values: dict


@router.put("/settings")
def put_app_settings(body: SettingsBody) -> dict:
    if not get_settings().settings_file:
        raise HTTPException(status_code=403, detail="settings are read-only on this deployment")
    changes: dict = {}
    for key, value in body.values.items():
        if key not in EDITABLE_KEYS:
            raise HTTPException(status_code=400, detail=f"unknown setting {key!r}")
        if key in _INT_KEYS:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} must be a number") from None
        elif not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{key} must be a string")
        changes[key] = value
    if changes.get("asr_device") == "cuda" and not _cuda_usable():
        raise HTTPException(
            status_code=400,
            detail="No usable NVIDIA GPU found — transcription stays on CPU. "
            "(GPU mode needs an NVIDIA card plus CUDA 12 and cuDNN 9 installed on this PC.)",
        )

    # Validate the LLM config that would result AFTER the change — a typo'd model
    # name must be rejected here, not discovered as a failed minutes job later.
    effective = {**_current_values(), **changes}
    if effective["llm_mode"] == "live":
        base = str(effective["llm_base_url"]).strip()
        model = str(effective["llm_model"]).strip()
        if not base.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="AI base URL must start with http(s)://")
        if not model:
            raise HTTPException(status_code=400, detail="pick or type an AI model name")
        if _is_ollama(base):
            local = "localhost" in base or "127.0.0.1" in base
            installed = _ollama_models(base, timeout=5.0, use_cache=False)
            if installed is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Ollama isn't running — start it (or choose another provider)"
                        if local
                        else f"Can't reach the Ollama server at {base} — check the "
                        "address and that it's up"
                    ),
                )
            if model not in installed:
                have = ", ".join(installed) if installed else "none installed"
                hint = f"Run: ollama pull {model}" if local else "Pick one of the listed models."
                raise HTTPException(
                    status_code=400,
                    detail=f"That server doesn't have '{model}'. Installed: {have}. {hint}",
                )

    save_overlay(changes)
    return _public()


class ListModelsBody(BaseModel):
    llm_base_url: str
    llm_api_key: str = ""


@router.post("/settings/list-models")
def list_llm_models(body: ListModelsBody) -> dict:
    """Models offered by the endpoint AS TYPED (nothing is saved) — fills the
    Settings model dropdown for hosted providers the same way local Ollama does."""
    from shruti_core.llm import list_models

    models, detail = list_models(body.llm_base_url, body.llm_api_key)
    return {"models": models, "detail": detail}


class TestLLMBody(BaseModel):
    llm_base_url: str
    llm_model: str
    llm_api_key: str = ""


@router.post("/settings/test-llm")
def test_llm(body: TestLLMBody) -> dict:
    """Try the AI settings AS TYPED (nothing is saved): one tiny chat call, so people
    can confirm the connection works before relying on it for minutes."""
    from shruti_core.llm import probe

    ok, detail = probe(body.llm_base_url, body.llm_model, body.llm_api_key)
    return {"ok": ok, "detail": detail}


class PrefetchBody(BaseModel):
    model: str


@router.post("/settings/prefetch-asr", status_code=202)
def prefetch_asr(body: PrefetchBody, db: Db) -> dict:
    """Download a whisper model now, as a job the UI can watch (progress bar),
    instead of a silent multi-minute stall on the first transcription."""
    choice = next((m for m in ASR_CHOICES if m["id"] == body.model), None)
    if choice is None:
        raise HTTPException(status_code=400, detail=f"unknown model {body.model!r}")
    job = jobs.enqueue(
        db,
        "asr_prefetch",
        queue="io",
        payload={"model": body.model, "total_mb": choice["sizeMB"]},
        max_attempts=1,
    )
    assert job is not None
    return job_public(job)
