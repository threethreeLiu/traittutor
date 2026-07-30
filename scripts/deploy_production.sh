#!/usr/bin/env bash
set -euo pipefail

# Deploy the current git HEAD to the single-host TraitTutor production server.
#
# The important Next.js standalone footgun:
#   `.next/standalone` does not include `.next/static` or `public`.
#   The systemd service runs from `web/.next/standalone`, so every release must
#   copy those assets into the standalone tree before switching `current`.

SERVER="${TRAITTUTOR_DEPLOY_SERVER:-ubuntu@106.54.196.207}"
BASE_DIR="${TRAITTUTOR_DEPLOY_BASE:-/var/www/traittutor}"
BASE_PATH="${TRAITTUTOR_DEPLOY_BASE_PATH:-/traittutor-all-web}"
API_PORT="${TRAITTUTOR_DEPLOY_API_PORT:-8002}"
WEB_PORT="${TRAITTUTOR_DEPLOY_WEB_PORT:-4091}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHA="$(git -C "$ROOT_DIR" rev-parse --short=7 HEAD)"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE="traittutor-${STAMP}-${SHA}"
ARCHIVE="/tmp/${RELEASE}.tar.gz"

echo "Deploying ${SHA} to ${SERVER}:${BASE_DIR}/releases/${RELEASE}"

if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
  echo "Refusing to deploy: local git worktree has uncommitted changes." >&2
  exit 1
fi

git -C "$ROOT_DIR" archive --format=tar --prefix="${RELEASE}/" HEAD | gzip > "$ARCHIVE"
scp "$ARCHIVE" "${SERVER}:/tmp/${RELEASE}.tar.gz"

ssh "$SERVER" "bash -s" -- \
  "$BASE_DIR" "$BASE_PATH" "$API_PORT" "$WEB_PORT" "$RELEASE" <<'REMOTE'
set -euo pipefail

BASE_DIR="$1"
BASE_PATH="$2"
API_PORT="$3"
WEB_PORT="$4"
RELEASE="$5"
RELEASE_DIR="${BASE_DIR}/releases/${RELEASE}"
ARCHIVE="/tmp/${RELEASE}.tar.gz"
PREVIOUS="$(readlink -f "${BASE_DIR}/current" 2>/dev/null || true)"

if [[ -e "$RELEASE_DIR" ]]; then
  echo "Refusing to deploy: release directory already exists: $RELEASE_DIR" >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR" "${BASE_DIR}/backups"
tar -xzf "$ARCHIVE" -C "$RELEASE_DIR" --strip-components=1

cd "$RELEASE_DIR"
"${BASE_DIR}/venv/bin/python" -m pip install -e .
"${BASE_DIR}/venv/bin/python" -c "import traittutor; import traittutor.api.main; print('python import ok')"

cd "$RELEASE_DIR/web"
NEXT_PUBLIC_BASE_PATH="$BASE_PATH" \
NEXT_PUBLIC_AUTH_ENABLED=true \
NEXT_PUBLIC_API_BASE=/api \
  npm ci

NEXT_PUBLIC_BASE_PATH="$BASE_PATH" \
NEXT_PUBLIC_AUTH_ENABLED=true \
NEXT_PUBLIC_API_BASE=/api \
  npm run build

# Required by Next.js standalone deployments. Without these, production may
# serve unstyled HTML and oversized raw SVG/icon shapes.
rm -rf .next/standalone/.next/static .next/standalone/public
cp -R .next/static .next/standalone/.next/static
cp -R public .next/standalone/public

test -f .next/standalone/server.js
test -d .next/standalone/.next/static
test -d .next/standalone/public
find .next/standalone/.next/static -name '*.css' -type f | grep -q .

echo "$PREVIOUS" > "${BASE_DIR}/backups/previous-release-before-${RELEASE}.txt"
ln -sfn "$RELEASE_DIR" "${BASE_DIR}/current"

rollback() {
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    echo "Health check failed; rolling back to $PREVIOUS" >&2
    ln -sfn "$PREVIOUS" "${BASE_DIR}/current"
    sudo systemctl restart traittutor-api traittutor-web
  fi
}
trap rollback ERR

sudo systemctl restart traittutor-api traittutor-web

for _ in {1..20}; do
  if curl -fsS "http://127.0.0.1:${API_PORT}/api/v1/auth/status" >/dev/null; then
    break
  fi
  sleep 1
done

curl -fsS "http://127.0.0.1:${API_PORT}/api/v1/auth/status" >/dev/null
curl -fsSI "http://127.0.0.1:${WEB_PORT}${BASE_PATH}/login" >/dev/null

CSS_PATH="$(find "${RELEASE_DIR}/web/.next/static" -name '*.css' -type f | head -1 | sed "s#^${RELEASE_DIR}/web/.next/static#${BASE_PATH}/_next/static#")"
test -n "$CSS_PATH"
curl -fsSI "http://127.0.0.1:${WEB_PORT}${CSS_PATH}" >/dev/null

trap - ERR
systemctl is-active traittutor-api traittutor-web >/dev/null
readlink -f "${BASE_DIR}/current"
echo "Deploy ok: ${RELEASE_DIR}"
REMOTE

echo "Done."
