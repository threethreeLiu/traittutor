#!/usr/bin/env bash
# Start the TraitTutor API and web app for local development.
# Both processes watch source files and reload automatically.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-3782}"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
BACKEND_PID=""
FRONTEND_PID=""
RESEARCH_WORKER_PID=""
REMINDER_WORKER_PID=""

# Background workers are the only optional local runtime process.
export TRAITTUTOR_BACKGROUND_WORKERS="${TRAITTUTOR_BACKGROUND_WORKERS:-1}"

terminate_tree() {
  local pid="$1"
  local child_pid
  for child_pid in $(pgrep -P "$pid" 2>/dev/null || true); do
    terminate_tree "$child_pid"
  done
  kill "$pid" 2>/dev/null || true
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    terminate_tree "$FRONTEND_PID"
  fi
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    terminate_tree "$BACKEND_PID"
  fi
  if [[ -n "$RESEARCH_WORKER_PID" ]] && kill -0 "$RESEARCH_WORKER_PID" 2>/dev/null; then
    terminate_tree "$RESEARCH_WORKER_PID"
  fi
  if [[ -n "$REMINDER_WORKER_PID" ]] && kill -0 "$REMINDER_WORKER_PID" 2>/dev/null; then
    terminate_tree "$REMINDER_WORKER_PID"
  fi
  wait "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$RESEARCH_WORKER_PID" 2>/dev/null || true
  wait "$REMINDER_WORKER_PID" 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

cd "$PROJECT_ROOT"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Creating Python virtual environment (.venv)..."
  python3 -m venv .venv
fi

# Hash the dependency manifests so *new* dependencies trigger a reinstall.
# The old probe (`import fastapi, uvicorn`) stayed green after pyproject.toml
# gained packages, leaving stale checkouts missing deps.
manifest_hash() {
  "$PYTHON_BIN" -c 'import hashlib, sys
digest = hashlib.sha256()
for path in sys.argv[1:]:
    with open(path, "rb") as handle:
        digest.update(handle.read())
print(digest.hexdigest())' "$@"
}

py_deps_stamp="$PROJECT_ROOT/.venv/.pyproject.sha256"
py_manifest_hash="$(manifest_hash "$PROJECT_ROOT/pyproject.toml")"
py_stamp_hash="$(cat "$py_deps_stamp" 2>/dev/null || true)"
if [[ "$py_stamp_hash" != "$py_manifest_hash" ]]; then
  echo "Installing Python dependencies (pyproject.toml new or changed)..."
  "$PYTHON_BIN" -m pip install -e .
  printf '%s\n' "$py_manifest_hash" > "$py_deps_stamp"
fi

web_deps_stamp="$PROJECT_ROOT/web/node_modules/.package-lock.sha256"
web_manifest_hash="$(manifest_hash "$PROJECT_ROOT/web/package.json" "$PROJECT_ROOT/web/package-lock.json")"
web_stamp_hash="$(cat "$web_deps_stamp" 2>/dev/null || true)"
if [[ "$web_stamp_hash" != "$web_manifest_hash" ]]; then
  echo "Installing frontend dependencies (package manifests new or changed)..."
  (cd "$PROJECT_ROOT/web" && npm ci)
  printf '%s\n' "$web_manifest_hash" > "$web_deps_stamp"
fi

"$PYTHON_BIN" - "$BACKEND_PORT" "$FRONTEND_PORT" <<'PY'
import socket
import sys

for value in sys.argv[1:]:
    port = int(value)
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise SystemExit(f"Port {port} is already in use: {error}")
PY

echo "Starting TraitTutor development servers..."
echo "  Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "  Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "Press Ctrl-C to stop both servers."

"$PYTHON_BIN" -m uvicorn traittutor.api.main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  --reload &
BACKEND_PID=$!

(
  cd "$PROJECT_ROOT/web"
  NEXT_PUBLIC_API_BASE="http://127.0.0.1:${BACKEND_PORT}" \
  TRAITTUTOR_API_BASE_URL="http://127.0.0.1:${BACKEND_PORT}" \
  npm run dev -- --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

if [[ "$TRAITTUTOR_BACKGROUND_WORKERS" == "1" ]]; then
  "$PYTHON_BIN" scripts/run_research_worker.py --poll-seconds 5 &
  RESEARCH_WORKER_PID=$!
  "$PYTHON_BIN" scripts/run_tutor_reminder_worker.py --poll-seconds 60 &
  REMINDER_WORKER_PID=$!
  echo "  Workers:   research + due-review reminders"
fi

all_alive() {
  kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null || return 1
  if [[ -n "$RESEARCH_WORKER_PID" ]]; then
    kill -0 "$RESEARCH_WORKER_PID" 2>/dev/null || return 1
  fi
  if [[ -n "$REMINDER_WORKER_PID" ]]; then
    kill -0 "$REMINDER_WORKER_PID" 2>/dev/null || return 1
  fi
}

# bash 3.2 on macOS lacks `wait -n`; poll so an unexpected exit shuts down
# the remaining process instead of leaving it running in the background.
while all_alive; do
  sleep 1
done

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  wait "$BACKEND_PID" || true
  echo "Backend stopped; shutting down frontend." >&2
elif ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
  wait "$FRONTEND_PID" || true
  echo "Frontend stopped; shutting down backend." >&2
else
  echo "A background worker stopped; shutting down development servers." >&2
fi
exit 1
