#!/usr/bin/env bash
set -Eeuo pipefail

# TraitTutor single-host deployment helper.
#
# The script deploys a committed HEAD to an Ubuntu server that already has:
#   - /var/www/traittutor/venv
#   - systemd units: traittutor-api.service and traittutor-web.service
#   - /var/lib/traittutor/config/models.local.yaml (private, mode 600)
#
# It creates an immutable release directory, builds the frontend inside that
# release, switches the `current` symlink atomically, checks API + web + CSS,
# and rolls back the symlink if the post-restart checks fail.
#
# By default the deploy also refuses to release without a calibrated BKT
# artifact ($TRAITTUTOR_HOME/config/bkt-parameters). Single-owner or demo
# servers that cannot meet the fixed calibration data floors may set
# TRAITTUTOR_DEPLOY_REQUIRE_CALIBRATED_BKT=0 for an explicit, auditable
# bypass; the runtime then stays on the uncalibrated cold start and keeps
# rendering "insufficient evidence" instead of mastery percentages.
#
# Usage:
#   TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com \
#     ./scripts/deploy_production.sh deploy
#   TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com \
#     ./scripts/deploy_production.sh status
#   TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com \
#     ./scripts/deploy_production.sh logs
#   TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com \
#     ./scripts/deploy_production.sh rollback [release-directory]
#
# The script deliberately deploys `git archive HEAD`, not the dirty working
# tree. Commit and push the intended release before deploying.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-deploy}"

SERVER="${TRAITTUTOR_DEPLOY_SERVER:-}"
BASE_DIR="${TRAITTUTOR_DEPLOY_BASE:-/var/www/traittutor}"
BASE_PATH="${TRAITTUTOR_DEPLOY_BASE_PATH:-/traittutor-all-web}"
API_PORT="${TRAITTUTOR_DEPLOY_API_PORT:-8002}"
WEB_PORT="${TRAITTUTOR_DEPLOY_WEB_PORT:-4091}"
REMOTE_VENV="${TRAITTUTOR_DEPLOY_VENV:-${BASE_DIR}/venv}"
TRAITTUTOR_HOME_REMOTE="${TRAITTUTOR_DEPLOY_HOME:-/var/lib/traittutor}"
ALLOW_DIRTY="${TRAITTUTOR_DEPLOY_ALLOW_DIRTY:-0}"
PROVIDER_SMOKE="${TRAITTUTOR_DEPLOY_PROVIDER_SMOKE:-1}"
REQUIRE_CALIBRATED_BKT="${TRAITTUTOR_DEPLOY_REQUIRE_CALIBRATED_BKT:-1}"
COURSEWARE_MODE="${TRAITTUTOR_DEPLOY_COURSEWARE_MODE:-deterministic}"
AGENTIC_ACCEPTANCE_REPORT="${TRAITTUTOR_AGENTIC_ACCEPTANCE_REPORT:-}"

# Optional SSH options, for example:
#   TRAITTUTOR_DEPLOY_SSH_OPTS='-i ~/.ssh/traittutor -o BatchMode=yes'
# Initialise explicitly: with `set -u`, an empty `read -a` leaves the array
# unset and `${SSH_OPTS[@]}` aborts before the first SSH preflight.
SSH_OPTS=()
if [[ -n "${TRAITTUTOR_DEPLOY_SSH_OPTS:-}" ]]; then
  read -r -a SSH_OPTS <<< "${TRAITTUTOR_DEPLOY_SSH_OPTS}"
fi

usage() {
  cat <<'USAGE'
Usage: scripts/deploy_production.sh <deploy|status|logs|rollback> [release]

Required for all remote actions:
  TRAITTUTOR_DEPLOY_SERVER=user@host

Optional environment:
  TRAITTUTOR_DEPLOY_BASE=/var/www/traittutor
  TRAITTUTOR_DEPLOY_BASE_PATH=/traittutor-all-web
  TRAITTUTOR_DEPLOY_API_PORT=8002
  TRAITTUTOR_DEPLOY_WEB_PORT=4091
  TRAITTUTOR_DEPLOY_VENV=/var/www/traittutor/venv
  TRAITTUTOR_DEPLOY_HOME=/var/lib/traittutor
  TRAITTUTOR_DEPLOY_SSH_OPTS='-i ~/.ssh/key -o BatchMode=yes'
  TRAITTUTOR_DEPLOY_ALLOW_DIRTY=1  # unsafe; deploys committed HEAD only
  TRAITTUTOR_DEPLOY_PROVIDER_SMOKE=0  # emergency bypass for provider outage
  TRAITTUTOR_DEPLOY_REQUIRE_CALIBRATED_BKT=0  # explicit bypass; BKT stays uncalibrated
  TRAITTUTOR_DEPLOY_COURSEWARE_MODE=deterministic|agentic
  TRAITTUTOR_AGENTIC_ACCEPTANCE_REPORT=/path/to/report.json  # required for agentic

Examples:
  TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com ./scripts/deploy_production.sh deploy
  TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com ./scripts/deploy_production.sh status
  TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com ./scripts/deploy_production.sh logs
  TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com ./scripts/deploy_production.sh rollback
USAGE
}

die() {
  echo "deploy: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_remote_config() {
  [[ -n "$SERVER" ]] || die "set TRAITTUTOR_DEPLOY_SERVER, for example ubuntu@example.com"
  require_command ssh
}

remote() {
  ssh "${SSH_OPTS[@]:-}" "$SERVER" "$@"
}

local_git_clean_check() {
  [[ "$ALLOW_DIRTY" == "1" ]] && {
    echo "WARNING: deploying committed HEAD while the local worktree is dirty" >&2
    return
  }

  if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
    die "local git worktree is dirty; commit the release first or set TRAITTUTOR_DEPLOY_ALLOW_DIRTY=1"
  fi
}

agentic_release_check() {
  if [[ "$COURSEWARE_MODE" == "deterministic" ]]; then
    return
  fi
  [[ "$COURSEWARE_MODE" == "agentic" ]] || die \
    "TRAITTUTOR_DEPLOY_COURSEWARE_MODE must be deterministic or agentic"
  [[ -n "$AGENTIC_ACCEPTANCE_REPORT" && -f "$AGENTIC_ACCEPTANCE_REPORT" ]] || die \
    "agentic production mode requires TRAITTUTOR_AGENTIC_ACCEPTANCE_REPORT"
  local sha python_bin
  sha="$(git -C "$ROOT_DIR" rev-parse HEAD)"
  python_bin="${ROOT_DIR}/.venv/bin/python"
  [[ -x "$python_bin" ]] || python_bin="$(command -v python3)"
  PYTHONPATH="$ROOT_DIR" "$python_bin" -m traittutor.orchestration.release_gate \
    "$AGENTIC_ACCEPTANCE_REPORT" --commit-sha "$sha" >/dev/null || die \
    "agentic acceptance report is missing, incomplete, or stale"
}

release_name() {
  local sha stamp
  sha="$(git -C "$ROOT_DIR" rev-parse --short=7 HEAD)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  printf 'traittutor-%s-%s\n' "$stamp" "$sha"
}

deploy() {
  require_remote_config
  require_command scp
  require_command git
  require_command gzip
  require_command tar
  local_git_clean_check
  agentic_release_check

  local release archive
  release="$(release_name)"
  archive="$(mktemp -t "${release}.XXXXXX.tar.gz")"
  previous=""
  trap 'rm -f "$archive"' EXIT

  echo "Preflight: ${SERVER}"
  remote "set -euo pipefail; sudo -n true; test -x '${REMOTE_VENV}/bin/python'; command -v npm >/dev/null; command -v curl >/dev/null; test -d '${BASE_DIR}/releases'; test -d '${TRAITTUTOR_HOME_REMOTE}'"

  echo "Packaging committed HEAD as ${release}"
  git -C "$ROOT_DIR" archive --format=tar --prefix="${release}/" HEAD | gzip > "$archive"
  scp "${SSH_OPTS[@]:-}" "$archive" "${SERVER}:/tmp/${release}.tar.gz"

  echo "Installing ${release} on ${SERVER}"
  ssh "${SSH_OPTS[@]:-}" "$SERVER" "bash -s" -- \
    "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$REMOTE_VENV" "$TRAITTUTOR_HOME_REMOTE" "$release" "$PROVIDER_SMOKE" "$COURSEWARE_MODE" "$REQUIRE_CALIBRATED_BKT" <<'REMOTE_DEPLOY'
set -Eeuo pipefail

BASE_DIR="$1"
BASE_PATH="$2"
API_PORT="$3"
WEB_PORT="$4"
REMOTE_VENV="$5"
TRAITTUTOR_HOME_REMOTE="$6"
RELEASE="$7"
PROVIDER_SMOKE="$8"
COURSEWARE_MODE="$9"
REQUIRE_CALIBRATED_BKT="${10}"
RELEASE_DIR="${BASE_DIR}/releases/${RELEASE}"
ARCHIVE="/tmp/${RELEASE}.tar.gz"
PREVIOUS="$(readlink -f "${BASE_DIR}/current" 2>/dev/null || true)"
API_SERVICE_USER="$(systemctl show traittutor-api.service -p User --value)"
API_SERVICE_USER="${API_SERVICE_USER:-root}"
API_SERVICE_GROUP="$(id -gn "$API_SERVICE_USER")"
export TRAITTUTOR_HOME="${TRAITTUTOR_HOME_REMOTE}"
export PYTHONPATH="$RELEASE_DIR"

cleanup() { rm -f "$ARCHIVE"; }
trap cleanup EXIT

[[ ! -e "$RELEASE_DIR" ]] || { echo "release already exists: $RELEASE_DIR" >&2; exit 1; }
mkdir -p "$RELEASE_DIR" "${BASE_DIR}/backups"
sudo install -d -m 755 "${TRAITTUTOR_HOME}/config"
tar -xzf "$ARCHIVE" -C "$RELEASE_DIR" --strip-components=1

# Keep provider credentials out of release directories. Migrate from one old
# manually-managed release only when the persistent runtime config is absent.
if [[ ! -f "${TRAITTUTOR_HOME}/config/models.local.yaml" && -n "$PREVIOUS" && -f "${PREVIOUS}/config/models.local.yaml" ]]; then
  sudo install -o "$API_SERVICE_USER" -g "$API_SERVICE_GROUP" -m 600 \
    "${PREVIOUS}/config/models.local.yaml" "${TRAITTUTOR_HOME}/config/models.local.yaml"
fi

cd "$RELEASE_DIR"
"${REMOTE_VENV}/bin/python" -m pip install -e .
PYTHONPATH="$RELEASE_DIR" "${REMOTE_VENV}/bin/python" -c \
  "import traittutor; import traittutor.api.main; print('python import ok')"
PYTHONPATH="$RELEASE_DIR" "${REMOTE_VENV}/bin/python" - <<'PY'
from traittutor.services.config import get_model_catalog_service

catalog = get_model_catalog_service().load()
llm = (catalog.get("services") or {}).get("llm") or {}
profiles = llm.get("profiles") or []
if not llm.get("active_profile_id") or not llm.get("active_model_id") or not profiles:
    raise SystemExit(
        "no active LLM profile loaded; configure "
        "$TRAITTUTOR_HOME/config/models.local.yaml"
    )
print("model catalog ok")
PY
if [[ "$REQUIRE_CALIBRATED_BKT" == "1" ]]; then
  PYTHONPATH="$RELEASE_DIR" "${REMOTE_VENV}/bin/python" - <<'PY'
from traittutor.learning_model import get_active_bkt_params

params = get_active_bkt_params()
if not params.calibrated:
    raise SystemExit("production deployment requires calibrated BKT parameters")
print(f"BKT parameters ok: {params.version} calibrated={params.calibrated}")
PY
else
  echo "WARNING: calibrated-BKT release gate explicitly bypassed; the deployment will run with uncalibrated BKT and learner-facing mastery keeps showing insufficient evidence" >&2
fi

if [[ "$PROVIDER_SMOKE" == "1" ]]; then
  PYTHONPATH="$RELEASE_DIR" "${REMOTE_VENV}/bin/python" \
    "$RELEASE_DIR/scripts/verify_gateway_provider.py"
else
  echo "WARNING: real Gateway provider smoke explicitly disabled" >&2
fi

cd "$RELEASE_DIR/web"
export NEXT_PUBLIC_BASE_PATH="$BASE_PATH"
export NEXT_PUBLIC_AUTH_ENABLED=true
export NEXT_PUBLIC_API_BASE=/api
npm ci
npm run build

# Next standalone output does not include these directories by default.
# Missing either one causes the deployed page to be unstyled or icon-less.
rm -rf .next/standalone/.next/static .next/standalone/public
cp -R .next/static .next/standalone/.next/static
cp -R public .next/standalone/public
test -f .next/standalone/server.js
test -d .next/standalone/.next/static
test -d .next/standalone/public
find .next/standalone/.next/static -type f -name '*.css' | grep -q .

"$RELEASE_DIR/scripts/install_production_units.sh" \
  "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$REMOTE_VENV" "$TRAITTUTOR_HOME" \
  "$API_SERVICE_USER" "$API_SERVICE_GROUP" "$COURSEWARE_MODE"

printf '%s\n' "$PREVIOUS" > "${BASE_DIR}/backups/previous-release-before-${RELEASE}.txt"
ln -sfn "$RELEASE_DIR" "${BASE_DIR}/current"

rollback_on_error() {
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    echo "Health check failed; rolling back to $PREVIOUS" >&2
    ln -sfn "$PREVIOUS" "${BASE_DIR}/current"
    sudo systemctl restart traittutor-api.service traittutor-web.service \
      traittutor-research-worker.service traittutor-reminder-worker.service
  fi
}
trap rollback_on_error ERR

sudo systemctl restart traittutor-api.service traittutor-web.service \
  traittutor-research-worker.service traittutor-reminder-worker.service

check_url() {
  local url="$1"
  local label="$2"
  for _ in {1..30}; do
    if curl --fail --silent --show-error "$url" >/dev/null; then
      echo "health ok: $label"
      return 0
    fi
    sleep 1
  done
  echo "health failed: $label ($url)" >&2
  return 1
}

check_url "http://127.0.0.1:${API_PORT}/api/v1/auth/status" "api"
curl --fail --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/login" >/dev/null
echo "health ok: web"
AUTH_REDIRECT_HEADERS="$(curl --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/home")"
printf '%s\n' "$AUTH_REDIRECT_HEADERS" | grep -Eq '^HTTP/[^ ]+ 30[1278]'
printf '%s\n' "$AUTH_REDIRECT_HEADERS" | tr -d '\r' | grep -Eiq "^location: .*${BASE_PATH}/login([?]|$)"
echo "health ok: unauthenticated workspace redirect"
check_url "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/api/v1/auth/status" "web api proxy"
CSS_PATH="$(find "${RELEASE_DIR}/web/.next/static" -type f -name '*.css' | head -1 | sed "s#^${RELEASE_DIR}/web/.next/static#${BASE_PATH}/_next/static#")"
[[ -n "$CSS_PATH" ]] || { echo "health failed: no built CSS" >&2; exit 1; }
curl --fail --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${CSS_PATH}" >/dev/null
echo "health ok: css ${CSS_PATH}"
sudo systemctl is-active --quiet traittutor-api.service
sudo systemctl is-active --quiet traittutor-web.service
sudo systemctl is-active --quiet traittutor-research-worker.service
sudo systemctl is-active --quiet traittutor-reminder-worker.service
echo "health ok: systemd"

trap - ERR
echo "active release: $(readlink -f "${BASE_DIR}/current")"
echo "deploy ok: ${RELEASE_DIR}"
REMOTE_DEPLOY

  echo "Deployment completed: ${release}"

  # The cleanup trap needs the function-local archive path. Remove it before
  # returning so that `set -u` does not evaluate an out-of-scope variable at
  # the script's final EXIT trap.
  trap - EXIT
  rm -f "$archive"
}

status() {
  require_remote_config
  ssh "${SSH_OPTS[@]:-}" "$SERVER" "bash -s" -- \
    "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$TRAITTUTOR_HOME_REMOTE" <<'REMOTE_STATUS'
set -euo pipefail
BASE_DIR="$1"
BASE_PATH="$2"
API_PORT="$3"
WEB_PORT="$4"
TRAITTUTOR_HOME_REMOTE="$5"
CURRENT="$(readlink -f "${BASE_DIR}/current" 2>/dev/null || true)"
echo "server: $(hostname)"
echo "current: ${CURRENT:-none}"
sudo systemctl --no-pager --full status \
  traittutor-api.service traittutor-web.service traittutor-research-worker.service \
  traittutor-reminder-worker.service | sed -n '1,60p'
curl --fail --silent --show-error "http://127.0.0.1:${API_PORT}/api/v1/auth/status" >/dev/null && echo "api: healthy"
curl --fail --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/login" >/dev/null && echo "web: healthy"
AUTH_REDIRECT_HEADERS="$(curl --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/home")"
printf '%s\n' "$AUTH_REDIRECT_HEADERS" | grep -Eq '^HTTP/[^ ]+ 30[1278]'
printf '%s\n' "$AUTH_REDIRECT_HEADERS" | tr -d '\r' | grep -Eiq "^location: .*${BASE_PATH}/login([?]|$)"
echo "unauthenticated workspace redirect: healthy"
curl --fail --silent --show-error "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/api/v1/auth/status" >/dev/null && echo "web api proxy: healthy"
if [[ -f "${TRAITTUTOR_HOME_REMOTE}/config/models.local.yaml" ]]; then
  echo "model config: present"
else
  echo "model config: missing (generation will be unavailable)"
fi
REMOTE_STATUS
}

logs() {
  require_remote_config
  remote "sudo journalctl -u traittutor-api.service -u traittutor-web.service -u traittutor-research-worker.service -u traittutor-reminder-worker.service -n 150 --no-pager"
}

rollback() {
  require_remote_config
  local target="${2:-}"
  ssh "${SSH_OPTS[@]:-}" "$SERVER" "bash -s" -- \
    "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$target" <<'REMOTE_ROLLBACK'
set -Eeuo pipefail
BASE_DIR="$1"
BASE_PATH="$2"
API_PORT="$3"
WEB_PORT="$4"
REQUESTED="$5"

if [[ -n "$REQUESTED" ]]; then
  TARGET="${BASE_DIR}/releases/${REQUESTED}"
else
  MARKER="$(find "${BASE_DIR}/backups" -maxdepth 1 -type f -name 'previous-release-before-*.txt' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)"
  [[ -n "$MARKER" && -f "$MARKER" ]] || { echo "no rollback marker found" >&2; exit 1; }
  TARGET="$(cat "$MARKER")"
fi

[[ -d "$TARGET" ]] || { echo "release not found: $TARGET" >&2; exit 1; }
echo "rolling back to $TARGET"
ln -sfn "$TARGET" "${BASE_DIR}/current"
sudo systemctl restart traittutor-api.service traittutor-web.service \
  traittutor-research-worker.service traittutor-reminder-worker.service
curl --fail --silent --show-error "http://127.0.0.1:${API_PORT}/api/v1/auth/status" >/dev/null
curl --fail --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/login" >/dev/null
AUTH_REDIRECT_HEADERS="$(curl --silent --show-error --head "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/home")"
printf '%s\n' "$AUTH_REDIRECT_HEADERS" | grep -Eq '^HTTP/[^ ]+ 30[1278]'
printf '%s\n' "$AUTH_REDIRECT_HEADERS" | tr -d '\r' | grep -Eiq "^location: .*${BASE_PATH}/login([?]|$)"
curl --fail --silent --show-error "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/api/v1/auth/status" >/dev/null
sudo systemctl is-active --quiet traittutor-api.service
sudo systemctl is-active --quiet traittutor-web.service
sudo systemctl is-active --quiet traittutor-research-worker.service
sudo systemctl is-active --quiet traittutor-reminder-worker.service
echo "rollback ok: $(readlink -f "${BASE_DIR}/current")"
REMOTE_ROLLBACK
}

case "$ACTION" in
  deploy) deploy ;;
  status) status ;;
  logs) logs ;;
  rollback) rollback ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
