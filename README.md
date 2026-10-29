# OpenClaw Knowledge Base Manager

A local administration backend for managing Markdown knowledge-base files, converting DOCX documents to Markdown, synchronizing OpenClaw agents, and running memory indexes.

## Development prerequisites

- Python 3.10+
- Node.js 20+
- OpenClaw installed and available on `PATH` for real integration runs

The repository can use the bundled Codex Python and Node runtimes during local verification.

Knowledge bases are linked to existing OpenClaw agents. The manager never creates agents automatically. Current OpenClaw configurations use `agents.entries.<id>` and `memory.search.extraPaths`; agent-level `memory.search.extraPaths` is combined with global paths, while legacy `agents.list[].memorySearch.extraPaths` and `agents.defaults.memorySearch.extraPaths` remain supported for migration. A single effective directory is selected automatically, while multiple directories remain selectable during creation. One OpenClaw agent can be linked to one managed knowledge base.

## Setup

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
pnpm --dir frontend install
```

Set a real `APP_SECRET_KEY` and administrator password before using the app. The initial password is imported into SQLite on first startup and can then be changed from the settings page.

## Run

```bash
uvicorn --env-file .env backend.main:app --reload --workers 1
pnpm --dir frontend dev
```

In production, build the frontend and serve it from FastAPI:

```bash
pnpm --dir frontend build
uvicorn --env-file .env backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Use one API worker because the application owns one SQLite job queue and one OpenClaw index lock.

## Server deployment

The deployment script assumes the server already has Python 3.10+, Node.js, pnpm, and OpenClaw. It does not install global dependencies, create or overwrite `.env`, or delete knowledge-base files.

1. Copy and edit the environment file:

   ```bash
   cp .env.example .env
   # set APP_SECRET_KEY, ADMIN_INITIAL_PASSWORD, and OpenClaw paths as needed
   ```

2. Build the application:

   ```bash
   ./scripts/deploy.sh
   ```

3. Install and start the systemd service when the server is ready:

   ```bash
   ./scripts/deploy.sh --install-service
   ```

The service runs with one Uvicorn worker and listens on the `APP_PORT` value from `.env` (or port `8000`). Use `--port` to override it for the generated systemd unit. The service user must have access to the configured `APP_DATA_DIR`, OpenClaw workspace, and mapped knowledge-base directories.

After changing `.env`, refresh the systemd unit and restart without reinstalling dependencies:

```bash
./scripts/deploy.sh --restart
```

On Ubuntu, if OpenClaw was installed through nvm or an npm user directory, configure its absolute executable path first:

```bash
command -v openclaw
# set OPENCLAW_BIN=/absolute/path/from-the-command-output in .env
./scripts/deploy.sh --restart
```

## Reverse proxy under a sub-path

To serve the manager from `http://<host>/kb-manage/` alongside other apps on
the same nginx server:

1. Set the external prefix in `.env`:

   ```bash
   APP_BASE_PATH=/kb-manage
   ```

   `APP_BASE_PATH` sets FastAPI's `root_path`, so generated URLs and the
   session cookie are scoped to `/kb-manage`. The frontend resolves asset and
   API URLs from an injected `<base href>`, so the same `frontend/dist` bundle
   works at the domain root and under any sub-path.

2. Forward requests **with the prefix intact** (no trailing slash on
   `proxy_pass`, so nginx passes `/kb-manage/...` through and FastAPI strips
   `root_path` itself):

   ```nginx
   location /kb-manage/ {
       proxy_pass http://127.0.0.1:8000;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
   }
   ```

3. If the site is plain `http://` (no TLS), allow a non-Secure session cookie
   or the browser will never store it:

   ```bash
   APP_COOKIE_SECURE=false
   ```

4. Rebuild the frontend if it was already built, then restart:

   ```bash
   pnpm --dir frontend build
   ./scripts/deploy.sh --restart
   ```

   Visiting `http://<host>/kb-manage/` now loads the UI, and its API calls land
   on `http://<host>/kb-manage/api/...`.
