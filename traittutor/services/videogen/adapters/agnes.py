"""Agnes ``agnes-video-v2.0`` submit, poll, and download adapter."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from traittutor.services.generation_http import (
    GenerationProviderError,
    build_auth_headers,
    join_api_path,
    raise_for_provider,
)
from traittutor.services.videogen.base import BaseVideogenAdapter, ProgressFn
from traittutor.services.videogen.config import VideogenConfig

_SUCCESS_STATES = {"succeeded", "success", "completed", "done"}
_FAILURE_STATES = {"failed", "error", "cancelled", "canceled", "expired"}


class AgnesVideogenAdapter(BaseVideogenAdapter):
    """Use Agnes' OpenAI-style ``/videos`` asynchronous task API."""

    @staticmethod
    def _headers(config: VideogenConfig) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            **build_auth_headers(config.auth_style, config.api_key),
            **(config.extra_headers or {}),
        }

    @staticmethod
    def _payload(prompt: str, config: VideogenConfig) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": config.model, "prompt": prompt}
        if config.aspect_ratio:
            payload["aspect_ratio"] = config.aspect_ratio
        if config.resolution:
            payload["resolution"] = config.resolution
        if config.duration:
            try:
                payload["duration"] = int(float(config.duration))
            except ValueError as exc:
                raise GenerationProviderError(
                    "Video duration must be a number of seconds."
                ) from exc
        return payload

    async def submit_task(self, prompt: str, config: VideogenConfig) -> str:
        if not config.base_url:
            raise GenerationProviderError("No endpoint URL configured for Agnes video generation.")
        try:
            async with httpx.AsyncClient(timeout=config.request_timeout) as client:
                for attempt in range(3):
                    response = await client.post(
                        join_api_path(config.base_url, "videos"),
                        headers=self._headers(config),
                        json=self._payload(prompt, config),
                    )
                    queue_full = response.status_code == 503 and "video_queue_full" in response.text
                    if queue_full and attempt < 2:
                        # Agnes' free video model has a shared queue. Retry only
                        # an explicit pre-submission queue rejection; ambiguous
                        # network failures are never retried because the server
                        # may already have accepted a billable task.
                        await asyncio.sleep(config.poll_interval * (attempt + 1))
                        continue
                    raise_for_provider(response, "Agnes video task submission")
                    break
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise GenerationProviderError(f"Agnes video task submission error: {detail}") from exc
        data = response.json()
        if isinstance(data, dict):
            for key in ("id", "video_id", "task_id"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        raise GenerationProviderError("Agnes video task submission returned no task id.")

    async def generate(
        self,
        prompt: str,
        config: VideogenConfig,
        *,
        progress: ProgressFn | None = None,
    ) -> tuple[bytes, str]:
        task_id = await self.submit_task(prompt, config)
        await self._notify(progress, "Submitted Agnes video task; rendering…")
        try:
            async with httpx.AsyncClient(timeout=config.request_timeout) as client:
                await self._poll(client, config, task_id, progress)
                response = await client.get(
                    join_api_path(config.base_url, f"videos/{task_id}/content"),
                    headers=self._headers(config),
                )
                raise_for_provider(response, "Agnes video download")
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise GenerationProviderError(
                f"Agnes video generation request error: {detail}"
            ) from exc
        if not response.content:
            raise GenerationProviderError("Agnes returned an empty video file.")
        content_type = response.headers.get("content-type") or "video/mp4"
        if not content_type.startswith("video/"):
            content_type = "video/mp4"
        return response.content, content_type

    async def _poll(
        self,
        client: httpx.AsyncClient,
        config: VideogenConfig,
        task_id: str,
        progress: ProgressFn | None,
    ) -> None:
        url = join_api_path(config.base_url, f"videos/{task_id}")
        deadline = time.monotonic() + config.poll_timeout
        last_progress = -1
        while True:
            response = await client.get(url, headers=self._headers(config))
            raise_for_provider(response, "Agnes video task status")
            data = response.json()
            if not isinstance(data, dict):
                raise GenerationProviderError("Malformed Agnes video task status response.")
            status = str(data.get("status") or data.get("state") or "").lower()
            try:
                percentage = int(data.get("progress") or 0)
            except (TypeError, ValueError):
                percentage = 0
            if status in _SUCCESS_STATES:
                await self._notify(progress, "Agnes video render completed.")
                return
            if status in _FAILURE_STATES:
                error = data.get("error") or data.get("message") or "no detail provided"
                raise GenerationProviderError(f"Agnes video task {task_id} {status}: {error}")
            if time.monotonic() >= deadline:
                raise GenerationProviderError(
                    f"Agnes video task timed out after {config.poll_timeout}s "
                    f"(last status: {status or 'unknown'})."
                )
            if percentage != last_progress:
                await self._notify(progress, f"Agnes video rendering: {percentage}%")
                last_progress = percentage
            await asyncio.sleep(config.poll_interval)

    @staticmethod
    async def _notify(progress: ProgressFn | None, message: str) -> None:
        if progress is not None:
            await progress(message)


__all__ = ["AgnesVideogenAdapter"]
