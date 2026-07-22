# Knowledge Base Management Backend

## Scope

This repository contains a single-machine knowledge-base management application for OpenClaw.
The application manages Markdown files, DOCX conversion, OpenClaw agent configuration, and index jobs.

## Structure

- `backend/`: FastAPI application, SQLite persistence, filesystem services, and the index worker.
- `frontend/`: React/Vite application. Build output is served by FastAPI in production.
- `tests/`: Python API/service tests and frontend checks.
- `runtime/`: local runtime data only. Never commit its contents.
- `scripts/`: local development and verification helpers.

## Conventions

- Python code uses type hints, `snake_case`, and small service modules.
- React components use `PascalCase`; browser-facing API data uses `camelCase` only at the frontend boundary.
- All filesystem paths are resolved before use. Never concatenate an untrusted filename into a path.
- Never invoke OpenClaw or conversion tools through a shell string. Pass argv arrays and capture stdout/stderr separately.
- Secrets belong in environment variables or the local runtime database, never in source, logs, or commits.
- Knowledge-base deletion detaches the mapping; it must not recursively delete the mapped directory.

## Verification

Use the bundled runtimes when available:

```bash
PYTHON=/Users/hujie/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
NODE=/Users/hujie/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
PNPM=/Users/hujie/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm

$PYTHON -m pytest
$PNPM --dir frontend build
```

For a local smoke test, run the API with one worker and the frontend through the Vite dev server. Use a temporary `APP_DATA_DIR` and fake OpenClaw executable in tests; never point tests at a real OpenClaw configuration.

## Cleanup

Runtime files and test temporary directories may be removed only when explicitly requested or by a documented cleanup command. Do not delete user-mapped knowledge-base directories.
