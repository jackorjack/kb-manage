"""Periodic mtime refresh so long-lived content keeps retrieval weight.

OpenClaw's retrieval score decays with file age. For knowledge bases that
rarely change but remain authoritative, files untouched for longer than the
configured age threshold would slowly sink in search results. A background
thread periodically bumps those files' mtime and queues an incremental index
so the scorer treats them as fresh.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import Settings
from .db import Database
from .files import list_markdown_files
from .jobs import JobError, create_job
from .utils import utc_now


def _as_bool(value: Optional[str], fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Optional[str], fallback: int) -> int:
    try:
        return int(value) if value is not None else fallback
    except ValueError:
        return fallback


class MtimeRefresher:
    """Background sweeper; effective config is env defaults overridden by the
    `settings` table so admins can tune it from the UI without a restart."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_run_at: Optional[str] = None
        self.last_touched = 0
        self.running = False

    # --- effective config: settings table overrides env ---------------------
    def _effective(self) -> Tuple[bool, int, int]:
        enabled = _as_bool(
            self.db.get_setting("refresher.enabled"), self.settings.refresh_job_enabled
        )
        age_days = _as_int(
            self.db.get_setting("refresher.age_days"), self.settings.refresh_job_age_days
        )
        interval = _as_int(
            self.db.get_setting("refresher.interval_seconds"),
            self.settings.refresh_job_interval_seconds,
        )
        return enabled, max(1, age_days), max(60, interval)

    def start(self) -> None:
        # The thread always runs; `enabled` only gates each tick, so toggling
        # it from the UI takes effect without a restart.
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="kb-mtime-refresher", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def run(self) -> None:
        # Stagger the first pass so startup doesn't fight the worker.
        if self.stop_event.wait(30):
            return
        while not self.stop_event.is_set():
            enabled, _, interval = self._effective()
            if enabled:
                self.tick()
            # Re-read every loop so a settings change takes effect next cycle
            # without restarting the app.
            self.stop_event.wait(interval)

    def tick(self) -> int:
        enabled, age_days, _ = self._effective()
        if not enabled:
            return 0
        self.running = True
        touched = 0
        try:
            threshold = time.time() - age_days * 86400
            now = time.time()
            for kb in self.db.fetchall(
                "SELECT id, path FROM knowledge_bases WHERE status = 'ready'"
            ):
                kb_touched = False
                for entry in list_markdown_files(Path(kb["path"])):
                    try:
                        if entry.stat().st_mtime < threshold:
                            os.utime(entry, (now, now))
                            touched += 1
                            kb_touched = True
                    except OSError:
                        # Read-only or vanished file: skip, never abort the sweep.
                        continue
                # Queue an incremental index so the scorer actually re-reads
                # the bumped mtimes; skipped silently when a job is in flight.
                if kb_touched:
                    try:
                        create_job(
                            self.db,
                            kb["id"],
                            "index",
                            "incremental",
                            {"source": "mtime-refresher"},
                            None,
                        )
                    except JobError:
                        pass
            self.last_touched = touched
            self.last_run_at = utc_now()
            return touched
        finally:
            self.running = False

    def status(self) -> Dict[str, Any]:
        enabled, age_days, interval = self._effective()
        return {
            "enabled": enabled,
            "ageDays": age_days,
            "intervalSeconds": interval,
            "running": self.running or bool(self.thread and self.thread.is_alive()),
            "lastRunAt": self.last_run_at,
            "lastTouched": self.last_touched,
            "defaults": {
                "enabled": self.settings.refresh_job_enabled,
                "ageDays": self.settings.refresh_job_age_days,
                "intervalSeconds": self.settings.refresh_job_interval_seconds,
            },
        }
