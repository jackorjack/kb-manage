# OpenClaw Knowledge Base Manager

A local administration backend for managing Markdown knowledge-base files, converting DOCX documents to Markdown, synchronizing OpenClaw agents, and running memory indexes.

## Development prerequisites

- Python 3.10+
- Node.js 20+
- OpenClaw installed and available on `PATH` for real integration runs

The repository can use the bundled Codex Python and Node runtimes during local verification.

Knowledge bases are linked to existing OpenClaw agents. The manager never creates agents automatically. When an agent has one `memorySearch.extraPaths` entry, its directory is used automatically; otherwise the directory is entered or selected during knowledge-base creation. One OpenClaw agent can be linked to one managed knowledge base.

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
