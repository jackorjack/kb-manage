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
            for key in ("agents", "items", "list", "entries"):
                if key in value:
                    return OpenClawService._agent_items(value[key])
            return [
                {**item, "id": item.get("id") or key}
                for key, item in value.items()
                if key != "defaults" and isinstance(item, dict)
            ]
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _agent_id(item: Dict[str, Any]) -> str:
        return str(item.get("id") or item.get("agentId") or "").strip()

    @classmethod
    def _memory_search(cls, item: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        memory = item.get("memory")
        if isinstance(memory, dict) and isinstance(memory.get("search"), dict):
            return memory["search"]
        legacy = item.get("memorySearch") or item.get("memory_search")
        if isinstance(legacy, dict):
            return legacy
        if isinstance(item.get("search"), dict):
            return item["search"]
        return {}

    @classmethod
    def _extra_paths(cls, item: Dict[str, Any]) -> List[str]:
        memory_search = cls._memory_search(item)
        if not isinstance(memory_search, dict):
            return []
        paths = memory_search.get("extraPaths", memory_search.get("extra_paths", []))
        if isinstance(paths, str):
            paths = [paths]
        return [path.strip() for path in paths if isinstance(path, str) and path.strip()]

    @classmethod
    def _has_extra_paths(cls, item: Dict[str, Any]) -> bool:
        memory_search = cls._memory_search(item)
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
        if "entries" in value:
            return defaults, self._agent_items(value.get("entries")), "entries"
        return defaults, self._agent_items(value.get("list", [])), "list"

    def _memory_config(self) -> Dict[str, Any]:
        try:
            value = self._json_command(["config", "get", "memory", "--json"])
        except OpenClawError:
            # Older configs may not have a top-level memory block yet.
            return {}
        if not isinstance(value, dict):
            raise OpenClawError("OpenClaw memory configuration has an unexpected format")
        return value

    @classmethod
    def _effective_paths(
        cls,
        agent: Dict[str, Any],
        defaults: Dict[str, Any],
        memory: Dict[str, Any],
        roster: str,
    ) -> tuple[List[str], str]:
        agent_paths = cls._extra_paths(agent)
        global_paths = cls._extra_paths(memory)
        default_paths = cls._extra_paths(defaults)
        if roster == "entries":
            raw_paths = [*global_paths, *default_paths, *agent_paths]
            source = "agent" if cls._has_extra_paths(agent) else "default"
        elif cls._has_extra_paths(agent):
            raw_paths = agent_paths
            source = "agent"
        else:
            raw_paths = [*global_paths, *default_paths]
            source = "default"
        return raw_paths, source if raw_paths else ""

    def list_agents(self) -> List[Dict[str, Any]]:
        listed = self._agent_items(self._json_command(["agents", "list", "--json"]))
        defaults, configured, roster = self._agents_config()
        memory = self._memory_config()
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
            raw_paths, path_source = self._effective_paths(own_config, defaults, memory, roster)
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
        defaults, agent_list, roster = self._agents_config()
        index = next(
            (i for i, item in enumerate(agent_list) if self._agent_id(item) == agent_id),
            None,
        )
        if index is None:
            raise OpenClawError(f"OpenClaw agent not found: {agent_id}")
        selector = agent_id if roster == "entries" else index
        return selector, agent_list[index], defaults, roster

    def configure_agent(self, agent_id: str, documents_path: Path) -> Dict[str, str]:
        selector, agent, defaults, roster = self._configured_agent(agent_id)
        memory = self._memory_config()
        workspace = str(
            agent.get("workspace")
            or agent.get("agentDir")
            or defaults.get("workspace")
            or ""
        )
        raw_paths, _ = self._effective_paths(agent, defaults, memory, roster)
        configured_paths = self._resolve_extra_paths(raw_paths, workspace)
        path = str(documents_path)
        if path not in configured_paths:
            if roster == "entries":
                config_path = f"agents.entries.{agent_id}.memory.search.extraPaths"
                write_paths = self._extra_paths(agent)
            else:
                config_path = f"agents.list[{selector}].memorySearch.extraPaths"
                source = agent if self._has_extra_paths(agent) else defaults
                write_paths = self._resolve_extra_paths(self._extra_paths(source), workspace)
            self.run(
                [
                    "config",
                    "set",
                    config_path,
                    json.dumps([*write_paths, path], ensure_ascii=False),
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
