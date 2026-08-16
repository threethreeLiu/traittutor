"""HTTP endpoint for chat attachment downloads / previews.

The chat turn runtime persists every uploaded attachment to the
:class:`~traittutor.services.storage.AttachmentStore` and records the public
URL on the message. The frontend preview drawer loads files via this
router, which only serves paths the store hands back — every component is
sanitised to defend against directory traversal.

URL shape::

    GET /api/attachments/{session_id}/{attachment_id}/{filename}

Access requires that ``session_id`` resolve to a session owned by the
authenticated user (owner-scoped session store lookup), and responses carry
a sandbox CSP: the web proxy serves ``/api/*`` same-origin, so uploaded
active content (HTML/SVG) is forced to a neutral download instead of
executing in the application origin.
"""

from __future__ import annotations

import logging
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from traittutor.api.utils.file_serving import (
    SANDBOX_CSP,
    content_disposition,
    is_active_content,
)
from traittutor.services.session import get_session_store
from traittutor.services.storage import (
    LocalDiskAttachmentStore,
    get_attachment_store,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _content_disposition(filename: str, *, disposition: str = "inline") -> str:
    """Backward-compatible wrapper over the shared header builder."""
    return content_disposition(filename, disposition=disposition)


@router.get("/{session_id}/{attachment_id}/{filename:path}")
async def get_attachment(
    session_id: str,
    attachment_id: str,
    filename: str,
):
    """Serve a previously uploaded chat attachment.

    Responds with ``Content-Disposition: inline`` so browsers preview PDFs
    and images directly in an ``<iframe>`` / ``<img>``. For unknown types
    the browser still falls back to download, which is fine for the
    drawer's "Download" button path.
    """
    store = get_attachment_store()
    if not isinstance(store, LocalDiskAttachmentStore):
        # Future remote backends should issue a redirect to the signed URL
        # here. Local-disk is the only backend today, so this branch just
        # guards against an unexpected configuration.
        raise HTTPException(status_code=501, detail="Attachment backend not servable")

    # Session ownership is the ACL, not the URL: the store lookup is scoped
    # to the authenticated user, so a foreign session id resolves to None.
    # The 404 matches the missing-file response and prevents enumeration.
    # (A global ``chat_attachment_dir`` override can place every user's
    # uploads under one root, which makes this check load-bearing.)
    session = await get_session_store().get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    target = store.resolve_path(
        session_id=session_id,
        attachment_id=attachment_id,
        filename=filename,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    media_type, _ = mimetypes.guess_type(target.name)
    if not media_type:
        media_type = "application/octet-stream"

    # Active content (HTML/SVG/XML...) must never render as a document in
    # this origin: the web proxy serves /api/* same-origin, so an uploaded
    # page would execute with the user's session cookies. Neutralize to a
    # download; passive previews (PDF, images, audio/video) stay inline.
    disposition = "inline"
    if is_active_content(target.name):
        media_type = "application/octet-stream"
        disposition = "attachment"

    headers = {
        "Content-Disposition": _content_disposition(target.name, disposition=disposition),
        # User-uploaded data; do not let intermediaries cache it.
        "Cache-Control": "private, max-age=0, must-revalidate",
        # Defense in depth: even a mis-sniffed type cannot script here.
        "Content-Security-Policy": SANDBOX_CSP,
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(path=str(target), media_type=media_type, headers=headers)
