#!/usr/bin/env bash
set -euo pipefail

export TRAITTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=1

# Container settings come only from canonical storage, never inherited host overrides.
unset BACKEND_PORT FRONTEND_PORT NEXT_PUBLIC_API_BASE_EXTERNAL NEXT_PUBLIC_API_BASE \
  CORS_ORIGIN CORS_ORIGINS DISABLE_SSL_VERIFY CHAT_ATTACHMENT_DIR AUTH_ENABLED \
  NEXT_PUBLIC_AUTH_ENABLED AUTH_USERNAME AUTH_PASSWORD_HASH AUTH_TOKEN_EXPIRE_HOURS \
  AUTH_COOKIE_SECURE TRAITTUTOR_API_BASE_URL TRAITTUTOR_AUTH_ENABLED

python - <<'PY'
from pathlib import Path
from traittutor.services.setup import init_user_directories

init_user_directories(Path("/app"))
PY

chown -R traittutor:traittutor /app/data 2>/dev/null || true

eval "$(python - <<'PY'
import shlex
from traittutor.services.config import export_runtime_settings_to_env

for key, value in export_runtime_settings_to_env(overwrite=True).items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)"

export BACKEND_PORT="${BACKEND_PORT:-8001}"
export FRONTEND_PORT="${FRONTEND_PORT:-3782}"

if (( $# )); then
  exec "$@"
fi

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
