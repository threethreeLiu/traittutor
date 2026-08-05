# TraitTutor production deployment

The supported production shape is a single Ubuntu host with two systemd
services:

```text
nginx / TLS
      ↓
Next.js standalone frontend :4091
      ↓ /api and /ws proxy
FastAPI backend :8002
```

The public ports are not exposed directly. Nginx forwards the application
base path to the frontend, and the frontend proxies `/api/*` and `/ws/*` to
the local backend. This avoids baking a private server address into the
browser bundle.

## 1. One-time server bootstrap

Use a deployment SSH account with passwordless access and interactive sudo:

```bash
export TRAITTUTOR_DEPLOY_SERVER=ubuntu@your-server

./scripts/bootstrap_production_server.sh
```

The bootstrap script installs Python, Node.js, npm, nginx, curl and git;
creates `/var/www/traittutor/{releases,backups}` and
`/var/lib/traittutor/config`; creates the virtual environment; and installs
the `traittutor-api.service` and `traittutor-web.service` units.

It does not copy credentials, configure a domain, or deploy application code.

## 2. Install the private model catalog

Create the deployment-only model file locally from the example, fill in the
provider details, and never commit it:

```bash
cp config/models.local.example.yaml /tmp/models.local.yaml
# edit /tmp/models.local.yaml
scp /tmp/models.local.yaml \
  "$TRAITTUTOR_DEPLOY_SERVER:/var/lib/traittutor/config/models.local.yaml"
ssh "$TRAITTUTOR_DEPLOY_SERVER" \
  'chmod 600 /var/lib/traittutor/config/models.local.yaml'
```

The release script refuses to switch traffic when no active LLM profile and
active model are present. This prevents the website from appearing healthy
while chat and generation are unusable.

## 3. Configure nginx and TLS

Create an nginx server block for the real domain. Keep the base path identical
to `TRAITTUTOR_DEPLOY_BASE_PATH` (the default is `/traittutor-all-web`):

```nginx
location /traittutor-all-web/ {
    proxy_pass http://127.0.0.1:4091;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
    client_max_body_size 210m;
}
```

Then validate and reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Use HTTPS in the real deployment. The application base path must match the
Next.js build path; changing it requires a new release build.

## 4. Deploy a release

The script deploys the committed `HEAD`, never the dirty working tree:

```bash
export TRAITTUTOR_DEPLOY_SERVER=ubuntu@your-server
export TRAITTUTOR_DEPLOY_BASE_PATH=/traittutor-all-web
export TRAITTUTOR_DEPLOY_API_PORT=8002
export TRAITTUTOR_DEPLOY_WEB_PORT=4091

git status --short
git add <intended-files>
git commit -m "release: prepare production build"
git push origin main

./scripts/deploy_production.sh deploy
```

The release script performs these checks and actions:

1. verifies SSH, the remote virtualenv, npm, curl and release directories;
2. archives the committed Git tree and uploads it to `/tmp`;
3. installs the Python package into the shared virtualenv;
4. verifies Python imports and the active model catalog;
5. runs `npm ci` and `npm run build` with the production base path;
6. copies `.next/static` and `public` into `.next/standalone` so CSS, icons,
   favicon and brand assets are served;
7. switches the `current` symlink atomically;
8. restarts both systemd services;
9. checks API auth status, web login, CSS and service health;
10. switches the symlink back automatically if post-deploy checks fail.

Useful deployment overrides:

```bash
export TRAITTUTOR_DEPLOY_SSH_OPTS='-i ~/.ssh/traittutor -o BatchMode=yes'
export TRAITTUTOR_DEPLOY_BASE=/srv/traittutor
export TRAITTUTOR_DEPLOY_HOME=/srv/traittutor-state
export TRAITTUTOR_DEPLOY_VENV=/srv/traittutor/venv
```

Do not use `TRAITTUTOR_DEPLOY_ALLOW_DIRTY=1` for a normal release. It only
allows the script to continue while still deploying committed `HEAD`, which
can make local changes appear to have been deployed when they were not.

## 5. Inspect, diagnose and rollback

```bash
./scripts/deploy_production.sh status
./scripts/deploy_production.sh logs
./scripts/deploy_production.sh rollback
```

To roll back to a named release:

```bash
./scripts/deploy_production.sh rollback traittutor-YYYYMMDD-HHMMSS-abcdef0
```

The `status` command reports the active release, systemd state, API/web
health, and whether the private model catalog exists. `logs` reads both
service journals. Rollback changes only the `current` symlink and restarts
the services; release directories are retained for inspection.

## 6. Release checklist

- `git status` is clean and the intended commit is on `main`.
- `models.local.yaml` exists on the server with mode `600` and an active LLM
  profile/model.
- `TRAITTUTOR_DEPLOY_BASE_PATH` matches the nginx location and public URL.
- The frontend build completes and contains CSS under `.next/standalone`.
- `status` reports API, web, and systemd healthy.
- Test login, model selection, chat, upload, learning canvas, and one
  generation path after deployment.
- If a release fails, inspect `logs`, run `rollback`, then preserve the failed
  release directory for diagnosis instead of deleting it immediately.
