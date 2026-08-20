import json

import pytest

from shruti_core.asr_types import AsrResult
from shruti_core.settings import get_settings
from shruti_worker.pipeline.asr import BACKENDS as ASR_BACKENDS


@pytest.fixture()
def fake_asr(monkeypatch, fixtures_dir):
    """Registers a canned-result ASR backend and selects it via settings."""
    result = AsrResult.model_validate(json.loads((fixtures_dir / "sample-asr.json").read_text()))

    class FakeBackend:
        name = "fake"

        def transcribe(self, wav_path, settings):
            return result

    monkeypatch.setitem(ASR_BACKENDS, "fake", FakeBackend())
    monkeypatch.setenv("ASR_BACKEND", "fake")
    get_settings.cache_clear()
    yield result
    get_settings.cache_clear()
