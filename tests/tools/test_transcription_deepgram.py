"""Behavior contracts for the native Deepgram STT provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_explicit_deepgram_without_key_fails_closed(monkeypatch):
    """An explicit Deepgram selection never falls through to another STT backend."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    with patch("tools.transcription_tools._HAS_FASTER_WHISPER", True):
        from tools.transcription_tools import _get_provider

        assert _get_provider({"provider": "deepgram"}) == "none"


@pytest.mark.parametrize(
    ("model", "prompt_parameter", "unsupported_parameter"),
    (
        ("nova-3", "keyterm", "keywords"),
        ("nova-3-medical", "keyterm", "keywords"),
        ("nova-30", "keywords", "keyterm"),
        ("legacy-nova-3", "keywords", "keyterm"),
        ("nova-2", "keywords", "keyterm"),
    ),
)
def test_deepgram_posts_audio_to_listen_with_shared_hints(
    monkeypatch, tmp_path, model, prompt_parameter, unsupported_parameter
):
    """The built-in maps the shared prompt to each Deepgram model's supported query contract."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    audio = tmp_path / "speech.wav"
    audio_bytes = b"RIFF-test-audio"
    audio.write_bytes(audio_bytes)
    captured = {}

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "results": {"channels": [{"alternatives": [{"transcript": "hello from deepgram"}]}]}
    }

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        captured["audio"] = kwargs["data"].read()
        return response

    config = {
        "provider": "deepgram",
        "language": "fr",
        "prompt": "Hermes Nous Research",
        "deepgram": {"model": model},
    }
    with patch("tools.transcription_tools._load_stt_config", return_value=config), \
         patch("requests.post", side_effect=fake_post):
        from tools.transcription_tools import _dispatch_stt_provider

        result = _dispatch_stt_provider(str(audio), "deepgram", config)

    assert result == {
        "success": True,
        "transcript": "hello from deepgram",
        "provider": "deepgram",
    }
    assert captured["url"] == "https://api.deepgram.com/v1/listen"
    assert captured["headers"] == {
        "Authorization": "Token dg-test",
        "Content-Type": "audio/wav",
    }
    assert captured["params"]["model"] == model
    assert captured["params"]["language"] == "fr"
    assert captured["params"][prompt_parameter] == "Hermes Nous Research"
    assert unsupported_parameter not in captured["params"]
    assert captured["audio"] == audio_bytes
