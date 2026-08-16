#!/usr/bin/env bash
set -euo pipefail

export PORT="${FRONTEND_PORT:-3782}"
export HOSTNAME="${FRONTEND_HOST:-0.0.0.0}"
exec node /app/web/server.js
