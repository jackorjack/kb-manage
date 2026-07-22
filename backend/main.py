"""FastAPI application for the knowledge-base manager."""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import Database
from .files import (
    FileValidationError,
    assert_paths_do_not_overlap,
    document_id,
    list_markdown_files,
    remove_staging_dir,
    resolve_knowledge_base_path,
    validate_upload_filename,
    save_upload,
)
from .jobs import JobError, JobWorker, create_job, get_active_job, get_job, update_job_payload
from .openclaw import OpenClawError, OpenClawService
from .schemas import (
    IndexRequest,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    LoginRequest,
    PasswordChangeRequest,
)
from .security import hash_password, new_csrf_token, new_session_token, verify_password
from .utils import json_loads, utc_now


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:48] or "knowledge-base"


def _expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()


def _serialize_job(row) -> Dict[str, Any]:
    if row is None:
        return None
    payload = json_loads(row["payload_json"], {})
    return {
        "id": row["id"],
        "knowledgeBaseId": row["knowledge_base_id"],
        "kind": row["kind"],
        "mode": row["mode"],
        "phase": row["phase"],
        "status": row["status"],
        "error": row["error"],
        "exitCode": row["exit_code"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "stdout": row["stdout"] or "",
        "stderr": row["stderr"] or "",
        "files": payload.get("files", []),
    }


def _serialize_document(row, path: Path) -> Dict[str, Any]:
    exists = path.is_file()
    stat = path.stat() if exists else None
    return {
        "id": row["id"],
        "name": row["name"],
        "sourceExt": row["source_ext"],
        "sizeBytes": stat.st_size if stat else row["size_bytes"],
        "modifiedAt": _modified_at(path) if exists else row["modified_at"],
        "sha256": row["sha256"],
        "exists": exists,
    }


def _serialize_kb(row, db: Database) -> Dict[str, Any]:
    active = get_active_job(db)
    latest = db.fetchone(
        "SELECT * FROM jobs WHERE knowledge_base_id = ? ORDER BY created_at DESC LIMIT 1",
        (row["id"],),
    )
    path = Path(row["path"])
    document_count = len(list_markdown_files(path))
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "path": row["path"],
        "agentId": row["agent_id"],
        "agentWorkspace": row["agent_workspace"],
        "status": row["status"],
        "error": row["error"],
        "documentCount": document_count,
        "isBusy": bool(active),
        "activeJob": _serialize_job(active) if active and active["knowledge_base_id"] == row["id"] else None,
        "latestJob": _serialize_job(latest),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _session(request: Request):
    db: Database = request.app.state.db
    token = request.cookies.get("kb_session")
    if not token:
        raise HTTPException(status_code=401, detail="login required")
    session = db.get_session(token)
    if session is None or session["expires_at"] <= utc_now():
        if session is not None:
            db.delete_session(token)
        raise HTTPException(status_code=401, detail="session expired")
    return session


def _write_guard(request: Request):
    session = _session(request)
    if request.headers.get("x-csrf-token") != session["csrf_token"]:
        raise HTTPException(status_code=403, detail="invalid csrf token")
    return session


def _require_kb(request: Request, knowledge_base_id: str):
    row = request.app.state.db.fetchone(
        "SELECT * FROM knowledge_bases WHERE id = ?", (knowledge_base_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return row


def _ready_kb(request: Request, knowledge_base_id: str):
    row = _require_kb(request, knowledge_base_id)
    if row["status"] != "ready":
        raise HTTPException(status_code=409, detail=row["error"] or "knowledge base is not ready")
    return row


def _assert_not_busy(db: Database) -> None:
    if get_active_job(db) is not None:
        raise HTTPException(status_code=409, detail="another job is already running")


def _reconcile_documents(db: Database, kb) -> List[Any]:
    path = Path(kb["path"])
    files = list_markdown_files(path)
    names = {file.name for file in files}
    with db.transaction(immediate=True) as connection:
        for file in files:
            stat = file.stat()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO documents(id, knowledge_base_id, name, source_ext, size_bytes, sha256, modified_at, created_at, updated_at)
                VALUES (?, ?, ?, '.md', ?, '', ?, ?, ?)
                ON CONFLICT(knowledge_base_id, name) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    modified_at = excluded.modified_at,
                    updated_at = excluded.updated_at
                """,
                (
                    document_id(kb["id"], file.name),
                    kb["id"],
                    file.name,
                    stat.st_size,
                    _modified_at(file),
                    now,
                    now,
                ),
            )
        if names:
            placeholders = ",".join("?" for _ in names)
            connection.execute(
                f"DELETE FROM documents WHERE knowledge_base_id = ? AND name NOT IN ({placeholders})",
                (kb["id"], *names),
            )
        else:
            connection.execute("DELETE FROM documents WHERE knowledge_base_id = ?", (kb["id"],))
    return db.fetchall(
        "SELECT * FROM documents WHERE knowledge_base_id = ? ORDER BY name COLLATE NOCASE",
        (kb["id"],),
    )


def _unique_slug(db: Database, name: str) -> str:
    base = _slugify(name)
    candidate = base
    index = 2
    while db.fetchone("SELECT id FROM knowledge_bases WHERE slug = ?", (candidate,)):
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    app_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        app_settings.staging_dir.mkdir(parents=True, exist_ok=True)
        db = Database(app_settings.database_path)
        db.initialize()
        db.seed_admin(app_settings.admin_username, app_settings.admin_initial_password)
        db.cleanup_sessions(utc_now())
        openclaw = OpenClawService(app_settings)
        worker = JobWorker(db, app_settings, openclaw)
        app.state.settings = app_settings
        app.state.db = db
        app.state.openclaw = openclaw
        app.state.worker = worker
        if app_settings.app_start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="OpenClaw Knowledge Base Manager", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.get("/api/health")
    def health():
        return {"ok": True, "service": "kb-manager"}

    @app.post("/api/auth/login")
    def login(request: Request, body: LoginRequest):
        db: Database = request.app.state.db
        user = db.get_user_by_username(body.username)
        if user is None or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="invalid username or password")
        token = new_session_token()
        csrf_token = new_csrf_token()
        db.create_session(token, user["id"], csrf_token, _expires_at(app_settings.session_ttl_seconds))
        response = {"user": {"username": user["username"]}, "csrfToken": csrf_token}
        from fastapi.responses import JSONResponse

        result = JSONResponse(response)
        result.set_cookie(
            "kb_session",
            token,
            max_age=app_settings.session_ttl_seconds,
            httponly=True,
            secure=app_settings.app_env == "production",
            samesite="lax",
            path="/",
        )
        return result

    @app.post("/api/auth/logout")
    def logout(request: Request):
        session = _write_guard(request)
        request.app.state.db.delete_session(request.cookies["kb_session"])
        from fastapi.responses import JSONResponse

        response = JSONResponse({"ok": True})
        response.delete_cookie("kb_session", path="/")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        session = _session(request)
        return {"user": {"username": session["username"]}, "csrfToken": session["csrf_token"]}

    @app.post("/api/auth/password")
    def change_password(request: Request, body: PasswordChangeRequest):
        session = _write_guard(request)
        user = request.app.state.db.get_user(session["user_id"])
        if user is None or not verify_password(body.currentPassword, user["password_hash"]):
            raise HTTPException(status_code=400, detail="current password is incorrect")
        request.app.state.db.update_password(session["user_id"], hash_password(body.newPassword))
        return {"ok": True}

    @app.get("/api/knowledge-bases")
    def list_knowledge_bases(request: Request):
        _session(request)
        db: Database = request.app.state.db
        rows = db.fetchall("SELECT * FROM knowledge_bases ORDER BY name COLLATE NOCASE")
        return {"items": [_serialize_kb(row, db) for row in rows]}

    @app.post("/api/knowledge-bases")
    def create_knowledge_base(request: Request, body: KnowledgeBaseCreate):
        session = _write_guard(request)
        db: Database = request.app.state.db
        try:
            path = resolve_knowledge_base_path(body.path)
            existing = [Path(row["path"]) for row in db.fetchall("SELECT path FROM knowledge_bases")]
            assert_paths_do_not_overlap(path, existing)
        except FileValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kb_id = str(uuid.uuid4())
        slug = _unique_slug(db, body.name)
        agent_id = f"kb-{uuid.uuid4().hex[:12]}"
        workspace = app_settings.openclaw_agent_workspace_root / agent_id
        now = utc_now()
        db.execute(
            """
            INSERT INTO knowledge_bases(id, name, slug, path, agent_id, agent_workspace, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'provisioning', ?, ?)
            """,
            (kb_id, body.name.strip(), slug, str(path), agent_id, str(workspace), now, now),
        )
        try:
            request.app.state.openclaw.ensure_agent(agent_id, workspace, path)
        except OpenClawError as exc:
            db.execute(
                "UPDATE knowledge_bases SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                (str(exc), utc_now(), kb_id),
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        db.execute(
            "UPDATE knowledge_bases SET status = 'ready', error = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), kb_id),
        )
        return _serialize_kb(db.fetchone("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)), db)

    @app.post("/api/knowledge-bases/{knowledge_base_id}/repair")
    def repair_knowledge_base(request: Request, knowledge_base_id: str):
        _write_guard(request)
        kb = _require_kb(request, knowledge_base_id)
        try:
            request.app.state.openclaw.ensure_agent(
                kb["agent_id"], Path(kb["agent_workspace"]), Path(kb["path"])
            )
        except OpenClawError as exc:
            request.app.state.db.execute(
                "UPDATE knowledge_bases SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                (str(exc), utc_now(), knowledge_base_id),
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        request.app.state.db.execute(
            "UPDATE knowledge_bases SET status = 'ready', error = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), knowledge_base_id),
        )
        return _serialize_kb(
            request.app.state.db.fetchone(
                "SELECT * FROM knowledge_bases WHERE id = ?", (knowledge_base_id,)
            ),
            request.app.state.db,
        )

    @app.patch("/api/knowledge-bases/{knowledge_base_id}")
    def update_knowledge_base(
        request: Request, knowledge_base_id: str, body: KnowledgeBaseUpdate
    ):
        _write_guard(request)
        kb = _ready_kb(request, knowledge_base_id)
        db: Database = request.app.state.db
        if body.path is not None and str(resolve_knowledge_base_path(body.path)) != kb["path"]:
            _assert_not_busy(db)
            documents = list_markdown_files(Path(kb["path"]))
            if documents:
                raise HTTPException(status_code=409, detail="cannot remap a knowledge base with documents")
            try:
                path = resolve_knowledge_base_path(body.path)
                existing = [
                    Path(row["path"])
                    for row in db.fetchall(
                        "SELECT path FROM knowledge_bases WHERE id != ?", (knowledge_base_id,)
                    )
                ]
                assert_paths_do_not_overlap(path, existing)
                request.app.state.openclaw.ensure_agent(
                    kb["agent_id"], Path(kb["agent_workspace"]), path
                )
            except (FileValidationError, OpenClawError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            db.execute(
                "UPDATE knowledge_bases SET path = ?, name = COALESCE(?, name), updated_at = ? WHERE id = ?",
                (str(path), body.name.strip() if body.name else None, utc_now(), knowledge_base_id),
            )
        elif body.name is not None:
            db.execute(
                "UPDATE knowledge_bases SET name = ?, updated_at = ? WHERE id = ?",
                (body.name.strip(), utc_now(), knowledge_base_id),
            )
        return _serialize_kb(db.fetchone("SELECT * FROM knowledge_bases WHERE id = ?", (knowledge_base_id,)), db)

    @app.delete("/api/knowledge-bases/{knowledge_base_id}")
    def delete_knowledge_base(request: Request, knowledge_base_id: str):
        _write_guard(request)
        _require_kb(request, knowledge_base_id)
        _assert_not_busy(request.app.state.db)
        request.app.state.db.execute("DELETE FROM knowledge_bases WHERE id = ?", (knowledge_base_id,))
        return {"ok": True, "physicalDirectoryPreserved": True}

    @app.get("/api/knowledge-bases/{knowledge_base_id}/documents")
    def list_documents(request: Request, knowledge_base_id: str):
        _session(request)
        kb = _ready_kb(request, knowledge_base_id)
        db: Database = request.app.state.db
        rows = _reconcile_documents(db, kb)
        return {
            "items": [
                _serialize_document(row, Path(kb["path"]) / row["name"]) for row in rows
            ]
        }

    @app.post("/api/knowledge-bases/{knowledge_base_id}/documents")
    async def upload_documents(
        request: Request,
        knowledge_base_id: str,
        files: List[UploadFile] = File(...),
    ):
        session = _write_guard(request)
        kb = _ready_kb(request, knowledge_base_id)
        db: Database = request.app.state.db
        _assert_not_busy(db)
        if not files or len(files) > 50:
            raise HTTPException(status_code=400, detail="upload between 1 and 50 files")
        stage = app_settings.staging_dir / f"upload-{uuid.uuid4().hex}"
        source_dir = stage / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        payload = {"staging_dir": str(stage), "files": []}
        targets = set()
        try:
            for upload in files:
                try:
                    source_name, extension, target_name = validate_upload_filename(upload.filename or "")
                    target_key = target_name.casefold()
                    if target_key in targets:
                        raise FileValidationError("duplicate target name in upload batch")
                    targets.add(target_key)
                    source_path = source_dir / source_name
                    size = await save_upload(upload, source_path, app_settings.max_upload_bytes)
                    payload["files"].append(
                        {
                            "source_name": source_name,
                            "source_ext": extension,
                            "target_name": target_name,
                            "source_path": str(source_path),
                            "size_bytes": size,
                            "status": "queued",
                        }
                    )
                finally:
                    await upload.close()
            job_id = create_job(db, knowledge_base_id, "upload", "incremental", payload, session["user_id"])
            return _serialize_job(get_job(db, job_id))
        except JobError as exc:
            remove_staging_dir(stage)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileValidationError as exc:
            remove_staging_dir(stage)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            remove_staging_dir(stage)
            raise

    @app.delete("/api/knowledge-bases/{knowledge_base_id}/documents/{document_id_value}")
    def delete_document(request: Request, knowledge_base_id: str, document_id_value: str):
        session = _write_guard(request)
        kb = _ready_kb(request, knowledge_base_id)
        db: Database = request.app.state.db
        _assert_not_busy(db)
        rows = _reconcile_documents(db, kb)
        row = next((item for item in rows if item["id"] == document_id_value), None)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        try:
            job_id = create_job(
                db,
                knowledge_base_id,
                "delete",
                "incremental",
                {"names": [row["name"]]},
                session["user_id"],
            )
        except JobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _serialize_job(get_job(db, job_id))

    @app.post("/api/knowledge-bases/{knowledge_base_id}/index")
    def index_knowledge_base(request: Request, knowledge_base_id: str, body: IndexRequest):
        session = _write_guard(request)
        _ready_kb(request, knowledge_base_id)
        try:
            job_id = create_job(
                request.app.state.db,
                knowledge_base_id,
                "index",
                "force" if body.force else "incremental",
                {},
                session["user_id"],
            )
        except JobError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _serialize_job(get_job(request.app.state.db, job_id))

    @app.get("/api/knowledge-bases/{knowledge_base_id}/index/status")
    def index_status(request: Request, knowledge_base_id: str):
        _session(request)
        _ready_kb(request, knowledge_base_id)
        db: Database = request.app.state.db
        active = db.fetchone(
            "SELECT * FROM jobs WHERE knowledge_base_id = ? AND status IN ('queued', 'running') ORDER BY created_at LIMIT 1",
            (knowledge_base_id,),
        )
        latest = db.fetchone(
            "SELECT * FROM jobs WHERE knowledge_base_id = ? ORDER BY created_at DESC LIMIT 1",
            (knowledge_base_id,),
        )
        return {"active": _serialize_job(active), "latest": _serialize_job(latest)}

    @app.get("/api/jobs/{job_id}")
    def job_detail(request: Request, job_id: str):
        _session(request)
        row = get_job(request.app.state.db, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _serialize_job(row)

    @app.get("/api/system/status")
    def system_status(request: Request):
        _session(request)
        settings = request.app.state.settings
        openclaw = request.app.state.openclaw.health()
        return {
            "openclaw": openclaw,
            "markitdown": {"ok": True, "mode": "in-process"},
            "database": {"ok": settings.database_path.exists(), "path": str(settings.database_path)},
            "worker": {
                "running": bool(request.app.state.worker.thread and request.app.state.worker.thread.is_alive())
            },
        }

    if app_settings.frontend_dist.exists():
        assets = app_settings.frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            return FileResponse(app_settings.frontend_dist / "index.html")

    return app


app = create_app()
