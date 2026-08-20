"""Central configuration switchboard.

The desktop exe seeds these via env vars in desktop/shruti_desktop.py (and the in-app
Settings tab overlays a JSON file on top — see userconfig.py). Application code never
branches on the environment; every knob lives here.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    database_url: str = "sqlite:///./data/shruti.db"
    storage_root: str = "./data/storage"

    # --- Worker ---
    worker_queues: str = "io,gpu"  # comma-separated queues this worker claims
    worker_poll_seconds: float = 2.0
    worker_heartbeat_seconds: float = 15.0
    job_stale_seconds: int = 300  # running job with heartbeat older than this gets requeued

    # --- ASR ---
    asr_backend: str = "faster_whisper"
    asr_model: str = "tiny"  # tiny (fast) … large-v3 (most accurate); picked in Settings
    asr_device: str = "cpu"  # cpu | cuda (needs a CUDA-enabled build/setup)
    asr_compute_type: str = "int8"  # int8 (cpu) | int8_float16 (cuda 6GB) | float16

    # --- Diarization (speaker separation) ---
    # Always on in the app (env-only kill switch kept for tests/emergencies)
    diarize_enabled: bool = True
    diarize_backend: str = "sherpa"  # tests swap in a canned backend via this knob
    sherpa_models_dir: str = ""  # where the sherpa backend keeps its ~40 MB of onnx models
    diarize_num_speakers: int = 0  # pin the speaker count when known (0 = auto-detect)

    # --- LLM (minutes & Q&A) ---
    llm_mode: str = "stub"  # stub | live
    llm_base_url: str = "http://localhost:11434/v1"  # local ollama / OpenAI-compatible endpoint
    llm_model: str = "qwen2.5:3b"
    llm_api_key: str = ""  # sent as Bearer when set (company OpenAI-compatible endpoints)
    # ALWAYS sent explicitly — ollama defaults to 4096 ctx and silently truncates
    llm_max_ctx: int = 16384

    # --- Desktop exe (desktop/) ---
    settings_file: str = ""  # JSON overlay written by the in-app Settings tab


@lru_cache
def get_settings() -> Settings:
    return Settings()
