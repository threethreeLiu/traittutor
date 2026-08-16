from fastapi import APIRouter

from . import connections, files, ingestion, kb_config, progress, providers
from ._shared import *  # noqa: F403 — historical tests reach helpers via knowledge.<name>

# One mounted contract: ``knowledge.router`` keeps the main.py include and
# route order (providers, kb config, connections, files, ingestion, progress)
# stable while each responsibility lives in its own module.
router = APIRouter()
for _sub in (providers, kb_config, connections, files, ingestion, progress):
    router.include_router(_sub.router)

__all__ = ["router"]
