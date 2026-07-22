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


if args == ["--version"]:
    print("fake-openclaw 1.0")
elif args[:3] == ["agents", "list", "--json"]:
    print(json.dumps(state["agents"]))
elif args[:2] == ["agents", "add"]:
    agent_id = args[2]
    workspace = args[args.index("--workspace") + 1]
    state["agents"].append({"id": agent_id, "workspace": workspace})
    Path(workspace).mkdir(parents=True, exist_ok=True)
    save()
    print(json.dumps({"id": agent_id}))
elif args[:4] == ["config", "get", "agents.list", "--json"]:
    print(json.dumps(state["agents"]))
elif args[:2] == ["config", "set"]:
    match = re.search(r"agents\.list\[(\d+)\]\.memorySearch\.extraPaths", args[2])
    if match:
        index = int(match.group(1))
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
else:
    print("unsupported fake command: " + " ".join(args), file=sys.stderr)
    save()
    raise SystemExit(2)

save()
