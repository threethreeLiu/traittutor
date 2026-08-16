#!/usr/bin/env bash
set -Eeuo pipefail

# Bootstrap the single-host Ubuntu environment expected by
# scripts/deploy_production.sh. This is intentionally separate from release
# deployment: run it once on a new server, review the generated systemd units,
# then use deploy_production.sh for every later release.
#
# Usage:
#   TRAITTUTOR_DEPLOY_SERVER=ubuntu@example.com \
#     ./scripts/bootstrap_production_server.sh
#
# Optional environment names are shared with deploy_production.sh:
#   TRAITTUTOR_DEPLOY_BASE, TRAITTUTOR_DEPLOY_API_PORT,
#   TRAITTUTOR_DEPLOY_WEB_PORT, TRAITTUTOR_DEPLOY_HOME,
#   TRAITTUTOR_DEPLOY_VENV, TRAITTUTOR_DEPLOY_SSH_OPTS

SERVER="${TRAITTUTOR_DEPLOY_SERVER:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="${TRAITTUTOR_DEPLOY_BASE:-/var/www/traittutor}"
API_PORT="${TRAITTUTOR_DEPLOY_API_PORT:-8002}"
WEB_PORT="${TRAITTUTOR_DEPLOY_WEB_PORT:-4091}"
BASE_PATH="${TRAITTUTOR_DEPLOY_BASE_PATH:-/traittutor-all-web}"
REMOTE_VENV="${TRAITTUTOR_DEPLOY_VENV:-${BASE_DIR}/venv}"
TRAITTUTOR_HOME_REMOTE="${TRAITTUTOR_DEPLOY_HOME:-/var/lib/traittutor}"

# Keep an empty SSH-options list defined under `set -u`; deployments normally
# rely on the user's SSH agent and do not need an explicit identity option.
SSH_OPTS=()
if [[ -n "${TRAITTUTOR_DEPLOY_SSH_OPTS:-}" ]]; then
  read -r -a SSH_OPTS <<< "${TRAITTUTOR_DEPLOY_SSH_OPTS}"
fi

if [[ -z "$SERVER" ]]; then
  echo "Set TRAITTUTOR_DEPLOY_SERVER, for example ubuntu@example.com" >&2
  exit 1
fi

ssh "${SSH_OPTS[@]:-}" "$SERVER" "bash -s" -- \
  "$BASE_DIR" "$REMOTE_VENV" "$TRAITTUTOR_HOME_REMOTE" <<'REMOTE_BOOTSTRAP'
set -Eeuo pipefail

BASE_DIR="$1"
REMOTE_VENV="$2"
TRAITTUTOR_HOME_REMOTE="$3"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

echo "Installing Ubuntu runtime packages (sudo may ask for your password)"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl git nginx nodejs npm python3 python3-pip python3-venv

node_major="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true)"
if [[ -z "$node_major" || "$node_major" -lt 20 ]]; then
  echo "Installing Node.js 22 because Next.js requires Node.js 20+"
  curl --fail --silent --show-error https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
node --version
npm --version

sudo install -d -o "$RUN_USER" -g "$RUN_GROUP" \
  "$BASE_DIR" "$BASE_DIR/releases" "$BASE_DIR/backups" \
  "$TRAITTUTOR_HOME_REMOTE/config"

if [[ ! -x "${REMOTE_VENV}/bin/python" ]]; then
  python3 -m venv "$REMOTE_VENV"
fi

sudo chown -R "$RUN_USER:$RUN_GROUP" "$BASE_DIR" "$TRAITTUTOR_HOME_REMOTE"
"${REMOTE_VENV}/bin/python" -m pip install --upgrade pip wheel
REMOTE_BOOTSTRAP

ssh "${SSH_OPTS[@]:-}" "$SERVER" "bash -s" -- \
  "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$REMOTE_VENV" "$TRAITTUTOR_HOME_REMOTE" \
  < "$ROOT_DIR/scripts/install_production_units.sh"

cat <<EOF
Bootstrap complete. Before the first deploy:
  1. Put the private model catalog at ${TRAITTUTOR_HOME_REMOTE}/config/models.local.yaml
  2. Restrict it with chmod 600.
  3. Proxy nginx/TLS to 127.0.0.1:${WEB_PORT}.
  4. Run scripts/deploy_production.sh deploy.
EOF
