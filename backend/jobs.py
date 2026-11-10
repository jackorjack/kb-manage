"""Persistent single-worker job queue for file changes and indexing."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import Settings
from .converter import DocxMarkdownConverter
from .db import Database
from .files import (
    FileValidationError,
    assert_target_path,
    document_id,
    list_markdown_files,
    move_document_file,
    normalize_markdown,
    publish_staged_outputs,
    remove_document_file,
    remove_staging_dir,
    sha256_file,
)
from .openclaw import (
    CommandResult,
    OpenClawError,
    OpenClawService,
    gateway_stop_reported_not_loaded,
)
from .utils import json_dumps, json_loads, utc_now


class JobError(RuntimeError):
    pass


def create_job(
    db: Database,
    knowledge_base_id: str,
    kind: str,
    mode: str,
    payload: Dict[str, Any],
    created_by: Optional[int],
) -> str:
    job_id = str(uuid.uuid4())
    now = utc_now()
    with db.transaction(immediate=True) as connection:
        active = connection.execute(
            "SELECT id FROM jobs WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        if active:
            raise JobError("another job is already running")
        connection.execute(
            """
            INSERT INTO jobs(id, knowledge_base_id, kind, mode, phase, status, payload_json, created_at, created_by)
            VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)
            """,
            (job_id, knowledge_base_id, kind, mode, json_dumps(payload), now, created_by),
        )
    return job_id


def update_job_payload(db: Database, job_id: str, payload: Dict[str, Any]) -> None:
    db.execute("UPDATE jobs SET payload_json = ? WHERE id = ?", (json_dumps(payload), job_id))


def get_job(db: Database, job_id: str):
    return db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))


def get_active_job(db: Database):
    return db.fetchone(
        "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at LIMIT 1"
    )


class JobWorker:
    def __init__(self, db: Database, settings: Settings, openclaw: OpenClawService):
        self.db = db
        self.settings = settings
        self.openclaw = openclaw
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.settings.app_data_dir.mkdir(parents=True, exist_ok=True)
        self.recover_running_jobs()
        self.thread = threading.Thread(target=self.run, name="kb-index-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        # Kill any in-flight OpenClaw subprocess so shutdown doesn't leave an
        # orphaned `memory index` running for up to the timeout window.
        self.openclaw.terminate_active()
        if self.thread:
            self.thread.join(timeout=3)

    def recover_running_jobs(self) -> None:
        self.db.execute(
            """
            UPDATE jobs
            SET status = 'failed', phase = 'failed', error = 'worker restarted before the job completed', finished_at = ?
            WHERE status = 'running'
            """,
            (utc_now(),),
        )

    def run(self) -> None:
        while not self.stop_event.is_set():
            row = self.claim_next()
            if row is None:
                self.stop_event.wait(0.5)
                continue
            self.process(row)

    def claim_next(self):
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = utc_now()
            connection.execute(
                "UPDATE jobs SET status = 'running', phase = 'starting', started_at = ?, heartbeat_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            return connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()

    def process(self, row) -> None:
        job_id = row["id"]
        payload = json_loads(row["payload_json"], {})
        try:
            if row["kind"] == "upload":
                self.process_upload(row, payload)
            elif row["kind"] == "delete":
                self.process_delete(row, payload)
            elif row["kind"] == "index":
                self.process_index(row, payload)
            elif row["kind"] == "move":
                self.process_move(row, payload)
            else:
                raise JobError(f"unsupported job kind: {row['kind']}")
            self.db.execute(
                "UPDATE jobs SET status = 'succeeded', phase = 'completed', finished_at = ?, heartbeat_at = ? WHERE id = ?",
                (utc_now(), utc_now(), job_id),
            )
        except Exception as exc:
            self.db.execute(
                "UPDATE jobs SET status = 'failed', phase = 'failed', error = ?, stderr = ?, finished_at = ?, heartbeat_at = ? WHERE id = ?",
                (str(exc), traceback.format_exc(limit=8), utc_now(), utc_now(), job_id),
            )
        finally:
            stage = payload.get("staging_dir")
            if stage:
                remove_staging_dir(Path(stage))

    def _set_phase(self, job_id: str, phase: str, payload: Optional[Dict[str, Any]] = None) -> None:
        now = utc_now()
        if payload is None:
            self.db.execute(
                "UPDATE jobs SET phase = ?, heartbeat_at = ? WHERE id = ?", (phase, now, job_id)
            )
        else:
            self.db.execute(
                "UPDATE jobs SET phase = ?, payload_json = ?, heartbeat_at = ? WHERE id = ?",
                (phase, json_dumps(payload), now, job_id),
            )

    def _knowledge_base(self, knowledge_base_id: str):
        row = self.db.fetchone(
            "SELECT * FROM knowledge_bases WHERE id = ? AND status = 'ready'", (knowledge_base_id,)
        )
        if row is None:
            raise JobError("knowledge base is not ready")
        return row

    def process_upload(self, row, payload: Dict[str, Any]) -> None:
        knowledge_base = self._knowledge_base(row["knowledge_base_id"])
        staging_dir = Path(payload["staging_dir"])
        converter = DocxMarkdownConverter(self.settings.max_conversion_output_bytes)
        outputs = []
        self._set_phase(row["id"], "converting")
        for item in payload.get("files", []):
            try:
                output_path = staging_dir / "outputs" / item["target_name"]
                converter.convert(Path(item["source_path"]), output_path, item["source_ext"])
                item["status"] = "converted"
                item["output_path"] = str(output_path)
                outputs.append(item)
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
                self._set_phase(row["id"], "conversion_failed", payload)
                raise JobError(f"{item['source_name']}: {exc}") from exc
            self._set_phase(row["id"], "converting", payload)

        self._set_phase(row["id"], "publishing", payload)
        publish_staged_outputs(Path(knowledge_base["path"]), outputs, staging_dir)
        with self.db.transaction(immediate=True) as connection:
            for item in outputs:
                target = assert_target_path(Path(knowledge_base["path"]), item["target_name"])
                stat = target.stat()
                now = utc_now()
                modified_at = datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).replace(microsecond=0).isoformat()
                connection.execute(
                    """
                    INSERT INTO documents(id, knowledge_base_id, name, source_ext, size_bytes, sha256, modified_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(knowledge_base_id, name) DO UPDATE SET
                        source_ext = excluded.source_ext,
                        size_bytes = excluded.size_bytes,
                        sha256 = excluded.sha256,
                        modified_at = excluded.modified_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        document_id(knowledge_base["id"], item["target_name"]),
                        knowledge_base["id"],
                        item["target_name"],
                        item["source_ext"],
                        stat.st_size,
                        sha256_file(target),
                        modified_at,
                        now,
                        now,
                    ),
                )
        self._set_phase(row["id"], "indexing", payload)
        result = self._run_index(row["id"], knowledge_base, self._index_target(knowledge_base), row["mode"] == "force")
        self._append_output(row["id"], result.stdout, result.stderr, result.returncode)

    def process_delete(self, row, payload: Dict[str, Any]) -> None:
        knowledge_base = self._knowledge_base(row["knowledge_base_id"])
        self._set_phase(row["id"], "deleting")
        names = payload.get("names", [])
        # Remove files first; filesystem deletes cannot roll back, so they must
        # not sit inside the DB transaction. The document row cleanup below is
        # a pure-DB transaction, and reconcile_documents self-heals any drift.
        for name in names:
            remove_document_file(Path(knowledge_base["path"]), name)
        with self.db.transaction(immediate=True) as connection:
            for name in names:
                connection.execute(
                    "DELETE FROM documents WHERE knowledge_base_id = ? AND name = ?",
                    (knowledge_base["id"], name),
                )
        self._set_phase(row["id"], "indexing")
        result = self._run_index(row["id"], knowledge_base, self._index_target(knowledge_base), False)
        self._append_output(row["id"], result.stdout, result.stderr, result.returncode)

    def process_index(self, row, payload: Dict[str, Any]) -> None:
        knowledge_base = self._knowledge_base(row["knowledge_base_id"])
        self._set_phase(row["id"], "indexing")
        result = self._run_index(row["id"], knowledge_base, self._index_target(knowledge_base), row["mode"] == "force")
        self._append_output(row["id"], result.stdout, result.stderr, result.returncode)

    def process_move(self, row, payload: Dict[str, Any]) -> None:
        """Move documents into another knowledge base and reindex both sides.

        Files move before any DB write because filesystem moves cannot roll
        back; the documents table is then rebuilt by reconciliation on both
        knowledge bases. The destination's agent must pick the content up too,
        so both targets are indexed.
        """
        source_kb = self._knowledge_base(row["knowledge_base_id"])
        target_kb = self._knowledge_base(payload["target_knowledge_base_id"])
        names = payload.get("names", [])
        self._set_phase(row["id"], "moving")
        moved = []
        for name in names:
            move_document_file(Path(source_kb["path"]), Path(target_kb["path"]), name)
            moved.append(name)
        now = utc_now()
        with self.db.transaction(immediate=True) as connection:
            for name in moved:
                connection.execute(
                    "DELETE FROM documents WHERE knowledge_base_id = ? AND name = ?",
                    (source_kb["id"], name),
                )
                target = assert_target_path(Path(target_kb["path"]), name)
                stat = target.stat()
                connection.execute(
                    """
                    INSERT INTO documents(id, knowledge_base_id, name, source_ext, size_bytes, sha256, modified_at, created_at, updated_at)
                    VALUES (?, ?, ?, '.md', ?, ?, ?, ?, ?)
                    ON CONFLICT(knowledge_base_id, name) DO UPDATE SET
                        size_bytes = excluded.size_bytes,
                        sha256 = excluded.sha256,
                        modified_at = excluded.modified_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        document_id(target_kb["id"], name),
                        target_kb["id"],
                        name,
                        stat.st_size,
                        sha256_file(target),
                        datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                        .replace(microsecond=0)
                        .isoformat(),
                        now,
                        now,
                    ),
                )
        self._set_phase(row["id"], "indexing")
        outputs = []
        for kb in (source_kb, target_kb):
            result = self._run_index(row["id"], kb, self._index_target(kb), False)
            outputs.append(f"[{kb['name']}]\n{result.stdout}{result.stderr}".strip())
        self._append_output(row["id"], "\n\n".join(outputs), "", 0)

    @staticmethod
    def _index_target(knowledge_base) -> Optional[str]:
        # Public knowledge bases live in global memory.search.extraPaths, so
        # every agent indexes them: run `memory index` without --agent.
        if knowledge_base["scope"] == "public":
            return None
        return knowledge_base["agent_id"]

    def _run_index(self, job_id: str, knowledge_base, agent_id: Optional[str], force: bool):
        # OpenClaw 11.x requires exclusive offline state ownership for every
        # `memory index` (incremental included) and refuses while a Gateway
        # holds the state dir. There is no online reindex RPC, so both modes
        # have to stop the Gateway for the duration of the index run.
        if force:
            # OpenClaw's retrieval score decays with file age. Refreshing the
            # mtime before a force reindex makes stable, long-lived content
            # rank as fresh instead of sinking over time.
            self._refresh_mtimes(Path(knowledge_base["path"]))
        return self._run_index_between_gateway_restarts(job_id, agent_id, force)

    def _run_index_between_gateway_restarts(
        self, job_id: str, agent_id: Optional[str], force: bool
    ) -> CommandResult:
        """Stop the Gateway, index, then always start it again.

        If the stop itself fails the Gateway is left alone and the job fails.
        Once it is down the restart runs even when indexing fails — leaving the
        agent service offline is worse than a failed job.
        """
        self._set_phase(job_id, "stopping_gateway")
        stop_result = self.openclaw.gateway_stop()
        # A Gateway that was not loaded was never ours to restart; starting it
        # would spawn a service the operator may not have installed.
        stopped = not gateway_stop_reported_not_loaded(stop_result)

        result: Optional[CommandResult] = None
        index_error: Optional[BaseException] = None
        try:
            with self.index_lock():
                self._set_phase(job_id, "force_indexing" if force else "indexing")
                result = self.openclaw.index(agent_id, force=force)
        except BaseException as exc:
            # Keep the index result even when the restart goes on to fail;
            # it carries the command output the job panel shows.
            index_error = exc

        self._set_phase(job_id, "starting_gateway")
        start_warning = ""
        start_error: Optional[OpenClawError] = None
        if stopped:
            try:
                start_warning = self._start_gateway_tolerating_plugin_errors()
            except OpenClawError as exc:
                start_error = exc

        if index_error is not None:
            if start_error is not None:
                raise JobError(
                    f"{index_error}; restarting the Gateway also failed: {start_error}"
                ) from start_error
            raise index_error
        if start_error is not None:
            raise start_error
        if start_warning:
            result = CommandResult(
                args=result.args,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr="\n".join(part for part in (start_warning, result.stderr) if part),
            )
        return result

    def _start_gateway_tolerating_plugin_errors(self) -> str:
        """Start the Gateway; return a warning when it is serving anyway.

        OpenClaw exits non-zero when an activated plugin fails to load, even
        though the Gateway process comes up and answers /readyz. Treat that as
        a warning so a stale third-party plugin (e.g. one built against an
        older plugin SDK) does not turn a successful force index into a failed
        job. A Gateway that is genuinely down still raises.
        """
        try:
            self.openclaw.gateway_start()
            return ""
        except OpenClawError as exc:
            if self.openclaw.gateway_is_running():
                return f"Gateway restarted with warnings: {exc}"
            raise

    @staticmethod
    def _refresh_mtimes(knowledge_base_path: Path) -> None:
        now = time.time()
        for entry in list_markdown_files(knowledge_base_path):
            try:
                os.utime(entry, (now, now))
            except OSError:
                # A read-only or disappearing file must not fail the index.
                continue

    @contextmanager
    def index_lock(self):
        self.settings.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings.lock_path.open("a+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _append_output(self, job_id: str, stdout: str, stderr: str, exit_code: int) -> None:
        self.db.execute(
            "UPDATE jobs SET stdout = ?, stderr = ?, exit_code = ?, heartbeat_at = ? WHERE id = ?",
            (stdout[-200_000:], stderr[-200_000:], exit_code, utc_now(), job_id),
        )
