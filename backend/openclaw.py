"""Safe argv-based integration with the OpenClaw CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Settings


class OpenClawError(RuntimeError):
    pass


@dataclass
class CommandResult:
    args: List[str]
    returncode: int
    stdout: str
    stderr: str


def _cap(value: str, limit: int = 200_000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


class OpenClawService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _environment(self) -> Dict[str, str]:
        environment = os.environ.copy()
        if self.settings.openclaw_config_path:
            environment["OPENCLAW_CONFIG_PATH"] = self.settings.openclaw_config_path
        return environment

    def run(self, arguments: List[str], timeout: Optional[int] = None) -> CommandResult:
        command = [self.settings.openclaw_bin, *arguments]
        try:
            completed = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=timeout or self.settings.openclaw_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OpenClawError(f"OpenClaw executable not found: {self.settings.openclaw_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenClawError("OpenClaw command timed out") from exc
        result = CommandResult(
            args=command,
            returncode=completed.returncode,
            stdout=_cap(completed.stdout or ""),
            stderr=_cap(completed.stderr or ""),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown OpenClaw error"
            raise OpenClawError(f"OpenClaw command failed ({result.returncode}): {detail}")
        return result

    def health(self) -> Dict[str, Any]:
        try:
            result = self.run(["--version"], timeout=15)
            return {"ok": True, "version": result.stdout.strip()}
        except OpenClawError as exc:
            return {"ok": False, "error": str(exc)}

    def _json_command(self, arguments: List[str]) -> Any:
        result = self.run(arguments)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OpenClawError("OpenClaw returned invalid JSON") from exc

    def ensure_agent(self, agent_id: str, workspace: Path, documents_path: Path) -> Dict[str, str]:
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            agents = self._json_command(["agents", "list", "--json"])
        except OpenClawError:
            agents = []
        if isinstance(agents, dict):
            agents = agents.get("agents", [])
        agents = agents if isinstance(agents, list) else []
        if not any(item.get("id") == agent_id for item in agents if isinstance(item, dict)):
            self.run(
                [
                    "agents",
                    "add",
                    agent_id,
                    "--workspace",
                    str(workspace),
                    "--non-interactive",
                    "--json",
                ]
            )

        agent_list = self._json_command(["config", "get", "agents.list", "--json"])
        if not isinstance(agent_list, list):
            raise OpenClawError("OpenClaw agents.list is not an array")
        index = next(
            (i for i, item in enumerate(agent_list) if isinstance(item, dict) and item.get("id") == agent_id),
            None,
        )
        if index is None:
            raise OpenClawError(f"OpenClaw agent was not created: {agent_id}")
        self.run(
            [
                "config",
                "set",
                f"agents.list[{index}].memorySearch.extraPaths",
                json.dumps([str(documents_path)], ensure_ascii=False),
                "--strict-json",
            ]
        )
        return {"agent_id": agent_id, "workspace": str(workspace), "path": str(documents_path)}

    def index(self, agent_id: str, force: bool = False) -> CommandResult:
        arguments = ["memory", "index", "--agent", agent_id]
        if force:
            arguments.append("--force")
        arguments.append("--verbose")
        return self.run(arguments)
