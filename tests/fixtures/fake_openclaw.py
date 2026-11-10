#!/usr/bin/env python3
"""Small deterministic OpenClaw CLI double used by integration tests."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


state_path = Path(os.environ["FAKE_OPENCLAW_STATE"])
state_path.parent.mkdir(parents=True, exist_ok=True)
if state_path.exists():
    state = json.loads(state_path.read_text())
else:
    state = {"agents": [], "commands": []}

args = sys.argv[1:]
state["commands"].append(args)


def save() -> None:
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def current_entries():
    config_agents = state.get("config", {}).get("agents", {})
    entries = config_agents.get("entries") if isinstance(config_agents, dict) else None
    if not isinstance(entries, dict):
        return None
    return [
        {**item, "id": item.get("id") or agent_id}
        for agent_id, item in entries.items()
        if isinstance(item, dict)
    ]


if args == ["--version"]:
    print("fake-openclaw 1.0")
elif args[:3] == ["agents", "list", "--json"]:
    entries = current_entries()
    print(json.dumps(state.get("agents", []) if entries is None else entries))
elif args[:2] == ["agents", "add"]:
    agent_id = args[2]
    workspace = args[args.index("--workspace") + 1]
    state["agents"].append({"id": agent_id, "workspace": workspace})
    Path(workspace).mkdir(parents=True, exist_ok=True)
    save()
    print(json.dumps({"id": agent_id}))
elif args[:4] == ["config", "get", "agents", "--json"]:
    config_agents = state.get("config", {}).get("agents")
    if isinstance(config_agents, dict):
        print(json.dumps(config_agents))
    else:
        print(json.dumps({"defaults": state.get("defaults", {}), "list": state.get("agents", [])}))
elif args[:4] == ["config", "get", "memory", "--json"]:
    print(json.dumps(state.get("config", {}).get("memory", state.get("memory", {}))))
elif args[:2] == ["config", "set"]:
    current_match = re.fullmatch(r"agents\.entries\.(.+)\.memory\.search\.extraPaths", args[2])
    legacy_match = re.fullmatch(r"agents\.list\[(\d+)\]\.memorySearch\.extraPaths", args[2])
    if args[2] == "memory.search.extraPaths":
        config = state.setdefault("config", {})
        memory = config.setdefault("memory", dict(state.get("memory", {})))
        memory.setdefault("search", {})["extraPaths"] = json.loads(args[3])
    elif current_match:
        agent_id = current_match.group(1)
        config = state.setdefault("config", {})
        entries = config.setdefault("agents", {}).setdefault("entries", {})
        entry = entries.setdefault(agent_id, {})
        entry.setdefault("memory", {}).setdefault("search", {})["extraPaths"] = json.loads(args[3])
    elif legacy_match:
        index = int(legacy_match.group(1))
        state["agents"][index].setdefault("memorySearch", {})["extraPaths"] = json.loads(args[3])
    save()
    print("ok")
elif args[:2] == ["memory", "index"]:
    if os.environ.get("FAKE_INDEX_FAIL") == "1":
        print("fake index failure", file=sys.stderr)
        save()
        raise SystemExit(2)
    print("indexed " + " ".join(args))
    save()
elif args[:2] == ["gateway", "stop"]:
    if os.environ.get("FAKE_GATEWAY_STOP_FAIL") == "1":
        print("fake gateway stop failure", file=sys.stderr)
        save()
        raise SystemExit(3)
    if os.environ.get("FAKE_GATEWAY_NOT_LOADED") == "1":
        print(json.dumps({"ok": True, "result": "not-loaded", "message": "Gateway service is not loaded."}))
        save()
        raise SystemExit(0)
    state["gateway_running"] = False
    print(json.dumps({"ok": True, "result": "stopped"}))
    save()
elif args[:2] == ["gateway", "start"]:
    if os.environ.get("FAKE_GATEWAY_START_FAIL") in {"1", "running"}:
        print(
            json.dumps(
                {
                    "action": "start",
                    "ok": False,
                    "error": "Gateway start failed: activated plugins reported load errors.",
                    "warnings": ["Activated plugin load errors:"],
                }
            ),
            file=sys.stderr,
        )
        if os.environ.get("FAKE_GATEWAY_START_FAIL") == "running":
            # Mimic OpenClaw: the process came up anyway, exit code still 1.
            state["gateway_running"] = True
        save()
        raise SystemExit(1)
    state["gateway_running"] = True
    print(json.dumps({"ok": True, "result": "started"}))
    save()
elif args[:2] == ["gateway", "status"]:
    running = bool(state.get("gateway_running"))
    print(
        json.dumps(
            {
                "service": {"loaded": running},
                "port": {"port": 18789, "status": "busy" if running else "free", "listeners": []},
            }
        )
    )
    save()
else:
    print("unsupported fake command: " + " ".join(args), file=sys.stderr)
    save()
    raise SystemExit(2)

save()
