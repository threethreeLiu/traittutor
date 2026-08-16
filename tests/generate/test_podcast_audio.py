from __future__ import annotations

import io
from pathlib import Path
import wave

import pytest

from traittutor.generate import podcast_audio
from traittutor.services.path_service import PathService
from traittutor.services.voice import VoiceProviderError

_SAMPLE_RATE = 24000
_CHANNELS = 1
_SAMPLE_WIDTH = 2


def _make_wav(duration_ms: int = 100) -> bytes:
    """Create a minimal valid WAV byte blob with silence of the given duration."""
    num_frames = int(_SAMPLE_RATE * duration_ms / 1000)
    audio = b"\x00" * (num_frames * _CHANNELS * _SAMPLE_WIDTH)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(_CHANNELS)
        wav.setsampwidth(_SAMPLE_WIDTH)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(audio)
    return buffer.getvalue()


_DIALOGUE = [
    {"speaker": "host", "text": "Welcome to the show."},
    {"speaker": "guest", "text": "Glad to be here."},
    {"speaker": "host", "text": "Let us dive in."},
    {"speaker": "guest", "text": "Sure thing."},
]


@pytest.mark.asyncio
async def test_podcast_audio_concatenates_multiple_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(podcast_audio, "get_path_service", lambda: service)

    call_log: list[str | None] = []

    async def fake_synthesize(
        text: str, *, voice: str | None = None, response_format: str | None = None, **_kw: object
    ) -> tuple[bytes, str]:
        call_log.append(voice)
        return _make_wav(50), "audio/wav"

    trace = await podcast_audio.synthesize_podcast_audio(
        _DIALOGUE,
        generation_id="gen-podcast-1",
        host_voice="alloy",
        synthesize=fake_synthesize,
    )

    assert trace["status"] == "completed"
    assert trace["audio_url"].startswith("/api/outputs/")
    assert trace["segment_count"] == 4
    # host turns use host_voice, guest turns use resolved guest voice
    assert call_log == ["alloy", "alloy", "alloy", "alloy"]

    wav_path = (
        service.get_task_workspace("chat", "traittutor-gen-podcast-1")
        / "media"
        / "learning-podcast.wav"
    )
    assert wav_path.exists()
    # The concatenated WAV must be valid and contain all frames + silence gaps
    with wave.open(str(wav_path), "rb") as wav:
        assert wav.getframerate() == _SAMPLE_RATE
        assert wav.getnchannels() == _CHANNELS
        # 4 segments × 50ms + 3 gaps × 350ms = 1250ms worth of frames
        expected_min_frames = int(_SAMPLE_RATE * 1200 / 1000)
        assert wav.getnframes() >= expected_min_frames


@pytest.mark.asyncio
async def test_podcast_audio_assigns_guest_voice_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(podcast_audio, "get_path_service", lambda: service)
    monkeypatch.setenv("TRAITTUTOR_PODCAST_VOICE_GUEST", "echo")

    call_log: list[str | None] = []

    async def fake_synthesize(
        text: str, *, voice: str | None = None, **_kw: object
    ) -> tuple[bytes, str]:
        call_log.append(voice)
        return _make_wav(30), "audio/wav"

    trace = await podcast_audio.synthesize_podcast_audio(
        _DIALOGUE,
        generation_id="gen-podcast-2",
        host_voice="alloy",
        synthesize=fake_synthesize,
    )

    assert trace["status"] == "completed"
    assert call_log == ["alloy", "echo", "alloy", "echo"]


@pytest.mark.asyncio
async def test_podcast_audio_degrades_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_synthesize(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
        raise VoiceProviderError("provider unavailable")

    trace = await podcast_audio.synthesize_podcast_audio(
        _DIALOGUE,
        generation_id="gen-podcast-fail",
        host_voice="alloy",
        synthesize=failing_synthesize,
    )

    assert trace["status"] == "failed"
    assert "provider unavailable" in trace["message"]
    assert "audio_url" not in trace


@pytest.mark.asyncio
async def test_podcast_audio_handles_empty_dialogue() -> None:
    async def unused_synthesize(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
        return _make_wav(30), "audio/wav"

    trace = await podcast_audio.synthesize_podcast_audio(
        [],
        generation_id="gen-podcast-empty",
        host_voice="alloy",
        synthesize=unused_synthesize,
    )

    assert trace["status"] == "failed"
    assert "empty dialogue" in trace["message"]
