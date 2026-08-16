from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.services.config.model_catalog import ModelCatalogService
from traittutor.services.config.provider_runtime import resolve_videogen_runtime_config
from traittutor.services.generation_http import GenerationProviderError
from traittutor.services.videogen.adapters.agnes import AgnesVideogenAdapter
from traittutor.services.videogen.config import VideogenConfig


def test_agnes_video_reuses_active_agnes_image_credential(tmp_path: Path) -> None:
    service = ModelCatalogService(tmp_path / "model-catalog.json")
    catalog = {
        "version": 1,
        "services": {
            "imagegen": {
                "active_profile_id": "image-profile",
                "active_model_id": "image-model",
                "profiles": [
                    {
                        "id": "image-profile",
                        "binding": "agnes",
                        "api_key": "secret-for-test",
                        "models": [{"id": "image-model", "model": "agnes-image-2.0-flash"}],
                    }
                ],
            },
            "videogen": {
                "active_profile_id": "video-profile",
                "active_model_id": "video-model",
                "profiles": [
                    {
                        "id": "video-profile",
                        "binding": "agnes",
                        "api_key": "",
                        "models": [{"id": "video-model", "model": "agnes-video-v2.0"}],
                    }
                ],
            },
        },
    }

    resolved = resolve_videogen_runtime_config(catalog=catalog, service=service)

    assert resolved.provider_name == "agnes"
    assert resolved.adapter == "agnes"
    assert resolved.model == "agnes-video-v2.0"
    assert resolved.api_key == "secret-for-test"


def test_agnes_video_payload_uses_numeric_duration() -> None:
    payload = AgnesVideogenAdapter._payload(
        "Explain photosynthesis",
        VideogenConfig(
            model="agnes-video-v2.0",
            aspect_ratio="16:9",
            duration="5",
            resolution="720p",
        ),
    )

    assert payload == {
        "model": "agnes-video-v2.0",
        "prompt": "Explain photosynthesis",
        "aspect_ratio": "16:9",
        "duration": 5,
        "resolution": "720p",
    }


def test_agnes_video_payload_rejects_invalid_duration() -> None:
    with pytest.raises(GenerationProviderError, match="duration"):
        AgnesVideogenAdapter._payload(
            "Explain photosynthesis",
            VideogenConfig(model="agnes-video-v2.0", duration="five"),
        )
