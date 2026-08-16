"""Authenticated download endpoint for generated user artifacts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from traittutor.api.utils.file_serving import (
    SANDBOX_CSP,
    content_disposition,
    forced_download_media_type,
    is_active_content,
)
from traittutor.services.path_service import get_path_service

router = APIRouter()


@router.get("/{path:path}")
async def download_output(path: str) -> FileResponse:
    """Serve only an artifact owned by the current request user.

    The router is mounted with ``require_auth`` in ``api.main``. Resolving the
    path inside the request keeps multi-user workspaces separate. Artifacts
    are model/code output served same-origin through the web proxy: every
    response is sandboxed by CSP, and active content (HTML/SVG/XML...) is
    forced to a neutral download instead of rendering as a document.
    """
    service = get_path_service()
    if not path or not service.is_public_output_path(path):
        raise HTTPException(status_code=404, detail="Output not found")
    candidate = (service.get_public_outputs_root() / path).resolve()
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": SANDBOX_CSP,
    }
    if is_active_content(candidate.name):
        headers["Content-Disposition"] = content_disposition(
            candidate.name, disposition="attachment"
        )
        return FileResponse(
            candidate,
            media_type=forced_download_media_type(candidate.name),
            headers=headers,
        )
    return FileResponse(candidate, headers=headers)
