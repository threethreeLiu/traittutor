#!/usr/bin/env bash
set -euo pipefail

if (( $# != 6 && $# != 8 && $# != 9 )); then
  echo "Usage: $0 BASE_DIR BASE_PATH API_PORT WEB_PORT VENV TRAITTUTOR_HOME [USER GROUP [COURSEWARE_MODE]]" >&2
  exit 2
fi

BASE_DIR="$1"
BASE_PATH="$2"
API_PORT="$3"
WEB_PORT="$4"
REMOTE_VENV="$5"
TRAITTUTOR_HOME="$6"
RUN_USER="${7:-$(id -un)}"
RUN_GROUP="${8:-$(id -gn "$RUN_USER")}"
COURSEWARE_MODE="${9:-deterministic}"

if [[ "$COURSEWARE_MODE" != "agentic" && "$COURSEWARE_MODE" != "deterministic" ]]; then
  echo "COURSEWARE_MODE must be agentic or deterministic" >&2
  exit 2
fi

install_unit() {
  local name="$1"
  sudo tee "/etc/systemd/system/${name}.service" >/dev/null
}

install_unit traittutor-api <<UNIT
[Unit]
Description=TraitTutor FastAPI backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${BASE_DIR}/current
Environment=TRAITTUTOR_HOME=${TRAITTUTOR_HOME}
Environment=PYTHONPATH=${BASE_DIR}/current
Environment=PYTHONUNBUFFERED=1
Environment=TRAITTUTOR_REQUIRE_CALIBRATED_BKT=1
Environment=TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE=${COURSEWARE_MODE}
ExecStart=${REMOTE_VENV}/bin/python -m uvicorn traittutor.api.main:app --host 127.0.0.1 --port ${API_PORT} --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

install_unit traittutor-web <<UNIT
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
Environment=TRAITTUTOR_API_BASE_URL=http://127.0.0.1:${API_PORT}
Environment=TRAITTUTOR_AUTH_ENABLED=true
ExecStart=/usr/bin/node ${BASE_DIR}/current/web/.next/standalone/server.js
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

install_unit traittutor-research-worker <<UNIT
[Unit]
Description=TraitTutor durable Research Workspace worker
After=network-online.target traittutor-api.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${BASE_DIR}/current
Environment=TRAITTUTOR_HOME=${TRAITTUTOR_HOME}
Environment=PYTHONPATH=${BASE_DIR}/current
Environment=PYTHONUNBUFFERED=1
Environment=TRAITTUTOR_REQUIRE_CALIBRATED_BKT=1
ExecStart=${REMOTE_VENV}/bin/python ${BASE_DIR}/current/scripts/run_research_worker.py --poll-seconds 5
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

install_unit traittutor-reminder-worker <<UNIT
[Unit]
Description=TraitTutor consent-aware due-review reminder worker
After=network-online.target traittutor-api.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${BASE_DIR}/current
Environment=TRAITTUTOR_HOME=${TRAITTUTOR_HOME}
Environment=PYTHONPATH=${BASE_DIR}/current
Environment=PYTHONUNBUFFERED=1
Environment=TRAITTUTOR_REQUIRE_CALIBRATED_BKT=1
ExecStart=${REMOTE_VENV}/bin/python ${BASE_DIR}/current/scripts/run_tutor_reminder_worker.py --poll-seconds 60
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable \
  traittutor-api.service \
  traittutor-web.service \
  traittutor-research-worker.service \
  traittutor-reminder-worker.service
