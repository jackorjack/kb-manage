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

    @staticmethod
    def _agent_items(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, dict):
            value = value.get("agents", value.get("items", []))
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _agent_id(item: Dict[str, Any]) -> str:
        return str(item.get("id") or item.get("agentId") or "").strip()

    @classmethod
    def _extra_paths(cls, item: Dict[str, Any]) -> List[str]:
        memory_search = item.get("memorySearch") or item.get("memory_search") or {}
        if not isinstance(memory_search, dict):
            return []
        paths = memory_search.get("extraPaths", memory_search.get("extra_paths", []))
        if isinstance(paths, str):
            paths = [paths]
        return [path.strip() for path in paths if isinstance(path, str) and path.strip()]

    @staticmethod
    def _has_extra_paths(item: Dict[str, Any]) -> bool:
        memory_search = item.get("memorySearch") or item.get("memory_search") or {}
        return isinstance(memory_search, dict) and (
            "extraPaths" in memory_search or "extra_paths" in memory_search
        )

    @staticmethod
    def _resolve_extra_paths(paths: List[str], workspace: str) -> List[str]:
        resolved: List[str] = []
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.is_absolute() and workspace:
                path = Path(workspace).expanduser() / path
            value = str(path.resolve()) if path.is_absolute() else raw_path
            if value not in resolved:
                resolved.append(value)
        return resolved

    def _agents_config(self):
        value = self._json_command(["config", "get", "agents", "--json"])
        if not isinstance(value, dict):
            raise OpenClawError("OpenClaw agents configuration has an unexpected format")
        defaults = value.get("defaults") if isinstance(value.get("defaults"), dict) else {}
        configured = self._agent_items(value.get("list", []))
        return defaults, configured

    def list_agents(self) -> List[Dict[str, Any]]:
        listed = self._agent_items(self._json_command(["agents", "list", "--json"]))
        defaults, configured = self._agents_config()
        listed_by_id = {self._agent_id(item): item for item in listed if self._agent_id(item)}
        configured_by_id = {
            self._agent_id(item): item for item in configured if self._agent_id(item)
        }
        result: List[Dict[str, Any]] = []
        seen = set()
        for item in [*listed, *configured]:
            agent_id = self._agent_id(item)
            if not agent_id or agent_id in seen:
                continue
            seen.add(agent_id)
            config = configured_by_id.get(agent_id, {})
            listed_item = listed_by_id.get(agent_id, {})
            workspace = str(
                listed_item.get("workspace")
                or listed_item.get("agentDir")
                or config.get("workspace")
                or config.get("agentDir")
                or defaults.get("workspace")
                or ""
            )
            own_config = config if config else listed_item
            if self._has_extra_paths(own_config):
                raw_paths = self._extra_paths(own_config)
                path_source = "agent"
            else:
                raw_paths = self._extra_paths(defaults)
                path_source = "default" if raw_paths else ""
            result.append(
                {
                    "id": agent_id,
                    "name": str(item.get("name") or config.get("name") or agent_id),
                    "workspace": workspace,
                    "extra_paths": self._resolve_extra_paths(raw_paths, workspace),
                    "path_source": path_source,
                }
            )
        return result

    def _configured_agent(self, agent_id: str):
        defaults, agent_list = self._agents_config()
        index = next(
            (i for i, item in enumerate(agent_list) if self._agent_id(item) == agent_id),
            None,
        )
        if index is None:
            raise OpenClawError(f"OpenClaw agent not found: {agent_id}")
        return index, agent_list[index], defaults

    def configure_agent(self, agent_id: str, documents_path: Path) -> Dict[str, str]:
        index, agent, defaults = self._configured_agent(agent_id)
        workspace = str(
            agent.get("workspace")
            or agent.get("agentDir")
            or defaults.get("workspace")
            or ""
        )
        source = agent if self._has_extra_paths(agent) else defaults
        configured_paths = self._resolve_extra_paths(self._extra_paths(source), workspace)
        path = str(documents_path)
        if path not in configured_paths:
            self.run(
                [
                    "config",
                    "set",
                    f"agents.list[{index}].memorySearch.extraPaths",
                    json.dumps([*configured_paths, path], ensure_ascii=False),
                    "--strict-json",
                ]
            )
        return {
            "agent_id": agent_id,
            "workspace": workspace,
            "path": str(documents_path),
        }

    def index(self, agent_id: str, force: bool = False) -> CommandResult:
        arguments = ["memory", "index", "--agent", agent_id]
        if force:
            arguments.append("--force")
        arguments.append("--verbose")
        return self.run(arguments)
