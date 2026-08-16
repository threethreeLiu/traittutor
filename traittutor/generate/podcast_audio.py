"""Two-host podcast audio synthesis and concatenation.

Mirrors the persistence pattern of :mod:`traittutor.generate.visuals` and
:mod:`traittutor.generate.videos`: each dialogue turn is synthesized with the
speaker's voice, all turns are concatenated into a single WAV using only the
stdlib ``wave`` module (no extra audio dependency), and the result is saved in
the authenticated public-output workspace.

Audio synthesis is an optional presentation layer.  A provider failure must
never turn a valid lesson into a failed generation task — callers receive a
``{"status": "failed", ...}`` trace and leave the component ``media_url``
empty so the frontend can fall back to its single-segment TTS path.
"""

from __future__ import annotations

from datetime import UTC, datetime
import io
import logging
import os
from typing import Any, Mapping, Protocol
from urllib.parse import quote
import wave

from traittutor.services.path_service import get_path_service
from traittutor.services.voice import VoiceProviderError, synthesize_speech

logger = logging.getLogger(__name__)

#: Force WAV so every turn shares a common PCM container that stdlib ``wave``
#: can read back and concatenate without a third-party audio library.
_PODCAST_FORMAT = "wav"
_PODCAST_CONTENT_TYPE = "audio/wav"
_PODCAST_SUFFIX = "wav"

#: Inter-turn silence in milliseconds, written as silence frames between turns
#: so the conversation does not sound rushed.  A short 350 ms gap is natural.
_INTER_TURN_SILENCE_MS = 350

#: Environment variable for the second speaker's provider voice name.
_GUEST_VOICE_ENV = "TRAITTUTOR_PODCAST_VOICE_GUEST"


class _SpeechSynthesizer(Protocol):
    """Callable shape of :func:`synthesize_speech` for dependency injection."""

    async def __call__(
        self,
        text: str,
        *,
        voice: str | None = ...,
        response_format: str | None = ...,
        speed: float | None = ...,
    ) -> tuple[bytes, str]: ...


def _resolve_guest_voice(host_voice: str | None) -> str | None:
    """Return the configured guest voice, or ``None`` to use the provider default."""
    configured = os.getenv(_GUEST_VOICE_ENV, "").strip()
    if configured:
        return configured
    # When host uses the provider default (None), let the guest use the default
    # too — the TTS catalog's configured voice is shared, but this is a graceful
    # degradation rather than a hard failure.
    return host_voice


def _read_wav_frames(audio: bytes) -> tuple[bytes, int, int]:
    """Extract raw PCM frames and params from a WAV byte blob.

    Returns ``(pcm_frames, sample_rate, channels)``.
    """
    with wave.open(io.BytesIO(audio), "rb") as wav:
        return (
            wav.readframes(wav.getnframes()),
            wav.getframerate(),
            wav.getnchannels(),
        )


def _concatenate_wav(segments: list[bytes]) -> bytes:
    """Concatenate WAV byte blobs into a single WAV, inserting silence between them."""
    if not segments:
        raise ValueError("No audio segments to concatenate.")
    if len(segments) == 1:
        return segments[0]

    parsed = [_read_wav_frames(seg) for seg in segments]
    sample_rate = parsed[0][1]
    channels = parsed[0][2]
    sample_width = 2  # PCM16 — matches voice._PCM16_SAMPLE_WIDTH
    silence_frames = int(sample_rate * _INTER_TURN_SILENCE_MS / 1000)
    silence = b"\x00" * (silence_frames * channels * sample_width)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(sample_rate)
        for index, (frames, _rate, _channels) in enumerate(parsed):
            out.writeframes(frames)
            if index < len(parsed) - 1:
                out.writeframes(silence)
    return buffer.getvalue()


async def synthesize_podcast_audio(
    dialogue: list[Mapping[str, str]],
    *,
    generation_id: str,
    host_voice: str | None,
    speed: float | None = None,
    synthesize: _SpeechSynthesizer = synthesize_speech,
) -> dict[str, Any]:
    """Synthesize and concatenate a two-host podcast dialogue into one WAV.

    ``host_voice`` is the provider voice name for the ``host`` speaker.  The
    ``guest`` speaker uses the voice configured via ``TRAITTUTOR_PODCAST_VOICE_GUEST``
    (falling back to the host voice).  Returns a trace dict with ``status``
    and, on success, ``audio_url``.
    """
    guest_voice = _resolve_guest_voice(host_voice)
    trace: dict[str, Any] = {
        "status": "unavailable",
        "turns": len(dialogue),
        "host_voice": host_voice or "default",
        "guest_voice": guest_voice or "default",
        "created_at": datetime.now(UTC).isoformat(),
    }
    if not dialogue:
        trace.update({"status": "failed", "message": "empty dialogue"})
        return trace

    segments: list[bytes] = []
    try:
        for turn in dialogue:
            speaker = turn.get("speaker", "host")
            text = str(turn.get("text", "")).strip()
            if not text:
                continue
            voice = host_voice if speaker == "host" else guest_voice
            audio, content_type = await synthesize(
                text,
                voice=voice,
                response_format=_PODCAST_FORMAT,
                speed=speed,
            )
            # The TTS router wraps PCM into WAV; some providers may still return
            # a raw PCM body with audio/pcm content type.  Normalize to WAV.
            if content_type.startswith("audio/pcm") or content_type.startswith("audio/x-pcm"):
                from traittutor.api.routers.voice import _parse_pcm_content_type, _pcm16_to_wav

                pcm_info = _parse_pcm_content_type(content_type)
                if pcm_info is not None:
                    sample_rate, channels = pcm_info
                    audio = _pcm16_to_wav(audio, sample_rate=sample_rate, channels=channels)
            segments.append(audio)
        if not segments:
            trace.update({"status": "failed", "message": "no turns produced audio"})
            return trace
        combined = _concatenate_wav(segments)
    except (ValueError, VoiceProviderError, OSError) as exc:
        logger.warning("Podcast audio synthesis failed: %s", exc)
        trace.update({"status": "failed", "message": str(exc)})
        return trace

    service = get_path_service()
    media_dir = service.get_task_workspace("chat", f"traittutor-{generation_id}") / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / f"learning-podcast.{_PODCAST_SUFFIX}"
    try:
        path.write_bytes(combined)
    except OSError as exc:
        logger.warning("Podcast audio persistence failed: %s", exc)
        trace.update({"status": "failed", "message": str(exc)})
        return trace

    relative = path.resolve().relative_to(service.get_public_outputs_root().resolve()).as_posix()
    url = "/api/outputs/" + quote(relative, safe="/")
    trace.update(
        {
            "status": "completed",
            "audio_url": url,
            "content_type": _PODCAST_CONTENT_TYPE,
            "segment_count": len(segments),
        }
    )
    return trace


__all__ = ["synthesize_podcast_audio"]
