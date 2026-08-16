from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.services.voice.adapters.minimax import MiniMaxTTSAdapter
from traittutor.services.voice.base import VoiceProviderError, VoiceProviderHTTPError
from traittutor.services.voice.config import TTSConfig


def _config(**overrides: Any) -> TTSConfig:
    base: dict[str, Any] = {
        "model": "speech-02-turbo",
        "api_key": "sk-test",
        "base_url": "https://api.minimaxi.com/v1",
        "voice": "male-qn-qingse",
        "response_format": "mp3",
    }
    base.update(overrides)
    return TTSConfig(**base)


def _response(status: int = 200, json_body: Any = None, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(status_code=status, text=text, json=lambda: json_body)


class _Client:
    def __init__(self, response: SimpleNamespace) -> None:
        self._response = response
        self.last_request: dict[str, Any] = {}

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> SimpleNamespace:
        self.last_request = {"url": url, **kwargs}
        return self._response


@pytest.mark.asyncio
async def test_minimax_tts_decodes_hex_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    mp3_prefix = b"ID3\x04\x00\x00\x00\x00\x00\x08"
    client = _Client(
        _response(
            json_body={
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "data": {"audio": mp3_prefix.hex()},
            }
        )
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)

    audio, content_type = await MiniMaxTTSAdapter().synthesize("hello", _config())

    assert audio == mp3_prefix
    assert content_type == "audio/mpeg"
    assert client.last_request["url"] == "https://api.minimaxi.com/v1/t2a_v2"
    payload = client.last_request["json"]
    assert payload["model"] == "speech-02-turbo"
    assert payload["voice_setting"]["voice_id"] == "male-qn-qingse"
    assert payload["audio_setting"]["format"] == "mp3"


@pytest.mark.asyncio
async def test_minimax_tts_wav_format(monkeypatch: pytest.MonkeyPatch) -> None:
    wav_prefix = b"RIFF\x00\x00\x00\x00WAVEfmt "
    client = _Client(
        _response(
            json_body={
                "base_resp": {"status_code": 0},
                "data": {"audio": wav_prefix.hex()},
            }
        )
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)

    audio, content_type = await MiniMaxTTSAdapter().synthesize(
        "hello", _config(response_format="wav")
    )

    assert audio == wav_prefix
    assert content_type == "audio/wav"


@pytest.mark.asyncio
async def test_minimax_tts_provider_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(
        _response(
            json_body={
                "base_resp": {"status_code": 1004, "status_msg": "text too long"},
                "data": {"audio": ""},
            }
        )
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)

    with pytest.raises(VoiceProviderError, match="1004"):
        await MiniMaxTTSAdapter().synthesize("hello", _config())


@pytest.mark.asyncio
async def test_minimax_tts_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(_response(status=401, text="unauthorized"))
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)

    with pytest.raises(VoiceProviderHTTPError):
        await MiniMaxTTSAdapter().synthesize("hello", _config())


@pytest.mark.asyncio
async def test_minimax_tts_rejects_non_hex_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(
        _response(
            json_body={
                "base_resp": {"status_code": 0},
                "data": {"audio": "not-hex-at-all"},
            }
        )
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: client)

    with pytest.raises(VoiceProviderError, match="hex"):
        await MiniMaxTTSAdapter().synthesize("hello", _config())
