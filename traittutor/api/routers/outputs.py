"""Authenticated download endpoint for generated user artifacts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from traittutor.services.path_service import get_path_service

router = APIRouter()


@router.get("/{path:path}")
async def download_output(path: str) -> FileResponse:
    """Serve only an artifact owned by the current request user.

    The router is mounted with ``require_auth`` in ``api.main``. Resolving the
    path inside the request keeps multi-user workspaces separate.
    """
    service = get_path_service()
    if not path or not service.is_public_output_path(path):
        raise HTTPException(status_code=404, detail="Output not found")
    candidate = (service.get_public_outputs_root() / path).resolve()
    return FileResponse(candidate, headers={"X-Content-Type-Options": "nosniff"})
