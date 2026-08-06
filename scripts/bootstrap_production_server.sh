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

ssh "${SSH_OPTS[@]}" "$SERVER" "bash -s" -- \
  "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$REMOTE_VENV" "$TRAITTUTOR_HOME_REMOTE" <<'REMOTE_BOOTSTRAP'
set -Eeuo pipefail

BASE_DIR="$1"
BASE_PATH="$2"
API_PORT="$3"
WEB_PORT="$4"
REMOTE_VENV="$5"
TRAITTUTOR_HOME_REMOTE="$6"
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

sudo tee /etc/systemd/system/traittutor-api.service >/dev/null <<UNIT
[Unit]
Description=TraitTutor FastAPI backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${BASE_DIR}/current
Environment=TRAITTUTOR_HOME=${TRAITTUTOR_HOME_REMOTE}
Environment=PYTHONPATH=${BASE_DIR}/current
Environment=PYTHONUNBUFFERED=1
ExecStart=${REMOTE_VENV}/bin/python -m uvicorn traittutor.api.main:app --host 127.0.0.1 --port ${API_PORT} --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/traittutor-web.service >/dev/null <<UNIT
[Unit]
Description=TraitTutor Next.js frontend
After=network-online.target traittutor-api.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${BASE_DIR}/current/web/.next/standalone
Environment=NODE_ENV=production
Environment=PORT=${WEB_PORT}
Environment=HOSTNAME=127.0.0.1
Environment=NEXT_PUBLIC_BASE_PATH=${BASE_PATH}
ExecStart=/usr/bin/node ${BASE_DIR}/current/web/.next/standalone/server.js
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable traittutor-api.service traittutor-web.service

echo
echo "Bootstrap complete. Before the first deploy:"
echo "  1. Put the private model catalog at ${TRAITTUTOR_HOME_REMOTE}/config/models.local.yaml"
echo "  2. Verify it is readable only by the deployment user: chmod 600 <file>"
echo "  3. Configure nginx/TLS to proxy the public path to 127.0.0.1:${WEB_PORT}"
echo "  4. Run scripts/deploy_production.sh deploy from the repository"
REMOTE_BOOTSTRAP
