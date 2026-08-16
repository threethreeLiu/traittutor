from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.generate import videos
from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.path_service import PathService


@pytest.mark.asyncio
async def test_learning_video_is_persisted_under_authenticated_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = PathService(workspace_root=tmp_path)

    async def generate(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
        return b"test-mp4", "video/mp4"

    monkeypatch.setattr(videos, "generate_video", generate)
    monkeypatch.setattr(videos, "get_path_service", lambda: service)

    trace = await videos.generate_learning_video(
        {
            "kind": "courseware",
            "title": "Photosynthesis",
            "sections": [{"core_content": "A leaf captures light energy."}],
            "component_id": "cmp-video",
        },
        generation_id="generation-video",
    )

    assert trace["status"] == "completed"
    assert trace["asset"]["content_type"] == "video/mp4"
    assert trace["asset"]["component_id"] == "cmp-video"
    assert trace["asset"]["url"].startswith("/api/outputs/")
    assert (
        service.get_task_workspace("chat", "traittutor-generation-video")
        / "media"
        / "learning-video.mp4"
    ).read_bytes() == b"test-mp4"


@pytest.mark.asyncio
async def test_learning_video_reports_provider_failure_without_persisting_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def generate(*_args: object, **_kwargs: object) -> tuple[bytes, str]:
        raise GenerationProviderError("provider unavailable")

    monkeypatch.setattr(videos, "generate_video", generate)

    trace = await videos.generate_learning_video(
        {"title": "Photosynthesis", "sections": [{"core_content": "A leaf captures light."}]},
        generation_id="generation-video-failure",
    )

    assert trace["status"] == "failed"
    assert trace["message"] == "provider unavailable"
    assert "asset" not in trace
