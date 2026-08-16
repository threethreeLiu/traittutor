#!/usr/bin/env bash
set -euo pipefail

ws_max_size="$(python -c 'from traittutor.services.config import get_ws_max_size; print(get_ws_max_size())' 2>/dev/null || printf '%s' 16777216)"
exec python -m uvicorn traittutor.api.main:app \
  --host "${BACKEND_HOST:-0.0.0.0}" \
  --port "${BACKEND_PORT:-8001}" \
  --no-access-log \
  --ws-max-size "$ws_max_size"
