"""MiniMax T2A v2 text-to-speech adapter.

MiniMax does not expose an OpenAI-compatible ``/audio/speech`` endpoint.
Its native synchronous API is ``POST {base}/t2a_v2`` and returns the
synthesized audio as a hex-encoded ``data.audio`` field.
"""

from __future__ import annotations

import binascii
import logging

import httpx

from traittutor.services.voice.base import (
    BaseTTSAdapter,
    VoiceProviderError,
    VoiceProviderHTTPError,
    build_auth_headers,
)
from traittutor.services.voice.config import TTSConfig

logger = logging.getLogger(__name__)

_T2A_PATH = "t2a_v2"
_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "flac": "audio/flac",
}


def _join_api_path(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix}"


class MiniMaxTTSAdapter(BaseTTSAdapter):
    """Synchronous MiniMax speech synthesis via the native T2A v2 API."""

    async def synthesize(self, text: str, config: TTSConfig) -> tuple[bytes, str]:
        if not config.base_url:
            raise VoiceProviderError("No endpoint URL configured for MiniMax TTS.")
        url = _join_api_path(config.base_url, _T2A_PATH)
        response_format = (config.response_format or "mp3").lower()
        if response_format not in _CONTENT_TYPES:
            response_format = "mp3"
        payload: dict[str, object] = {
            "model": config.model,
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": config.voice or ""},
            "audio_setting": {"format": response_format, "sample_rate": 32000},
        }
        if config.speed is not None:
            payload["voice_setting"] = {**payload["voice_setting"], "speed": float(config.speed)}  # type: ignore[dict-item]
        headers = {
            "Content-Type": "application/json",
            **build_auth_headers(config.auth_style, config.api_key),
            **(config.extra_headers or {}),
        }
        logger.debug(
            "MiniMax TTS synthesize url=%s model=%s voice=%s fmt=%s chars=%d",
            url,
            config.model,
            config.voice,
            response_format,
            len(text),
        )
        try:
            async with httpx.AsyncClient(timeout=config.request_timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise VoiceProviderError(f"MiniMax TTS request error: {exc}") from exc
        if resp.status_code >= 400:
            raise VoiceProviderHTTPError(
                f"MiniMax TTS returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text[:400],
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise VoiceProviderError("MiniMax TTS returned non-JSON response") from exc
        base_resp = body.get("base_resp") or {}
        if int(base_resp.get("status_code") or 0) != 0:
            raise VoiceProviderError(
                "MiniMax TTS error "
                f"{base_resp.get('status_code')}: {base_resp.get('status_msg') or 'unknown'}"
            )
        data = body.get("data") or {}
        hex_audio = data.get("audio")
        if not hex_audio:
            raise VoiceProviderError(
                f"MiniMax TTS returned no audio: {body.get('extra_info') or ''}"
            )
        try:
            audio = binascii.unhexlify(hex_audio)
        except (binascii.Error, ValueError) as exc:
            raise VoiceProviderError("MiniMax TTS audio payload is not valid hex") from exc
        if not audio:
            raise VoiceProviderError("MiniMax TTS returned empty audio.")
        return audio, _CONTENT_TYPES[response_format]


__all__ = ["MiniMaxTTSAdapter"]
