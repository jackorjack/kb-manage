from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from tests.test_converter import minimal_docx


FIXTURE = Path(__file__).parent / "fixtures" / "fake_openclaw.py"


def wait_for_job(client: TestClient, job_id: str):
    deadline = time.time() + 12
    while time.time() < deadline:
        result = client.get(f"/api/jobs/{job_id}")
        assert result.status_code == 200
        job = result.json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.15)
    raise AssertionError("job did not finish")


def make_client(tmp_path, monkeypatch, state=None):
    state_path = tmp_path / "openclaw-state.json"
    state_path.write_text(json.dumps(state or {
        "defaults": {},
        "agents": [
            {
                "id": "assistant",
                "name": "Assistant",
                "workspace": str(tmp_path / "agent-workspace"),
            }
        ],
        "commands": [],
    }))
    monkeypatch.setenv("FAKE_OPENCLAW_STATE", str(state_path))
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "APP_DATA_DIR": str(tmp_path / "runtime"),
            "ADMIN_USERNAME": "admin",
            "ADMIN_INITIAL_PASSWORD": "correct-horse-battery",
            "OPENCLAW_BIN": str(FIXTURE),
            "APP_START_WORKER": "true",
            # Keep the default public directory inside the test sandbox.
            "PUBLIC_KB_PATH": str(tmp_path / "kb" / "public"),
        }
    )
    return TestClient(create_app(settings)), state_path


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    return response.json()["csrfToken"]


def test_knowledge_base_upload_index_and_delete(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        kb_path = tmp_path / "docs"
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "产品文档", "agentId": "assistant", "path": str(kb_path)},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        kb = created.json()

        upload = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("guide.md", b"# Guide\n\nHello", "text/markdown"))],
            headers=headers,
        )
        assert upload.status_code == 200, upload.text
        job = wait_for_job(client, upload.json()["id"])
        assert job["status"] == "succeeded", job
        assert (kb_path / "guide.md").read_text() == "# Guide\n\nHello\n"

        docx_upload = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[
                (
                    "files",
                    (
                        "converted.docx",
                        minimal_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            ],
            headers=headers,
        )
        assert docx_upload.status_code == 200, docx_upload.text
        docx_job = wait_for_job(client, docx_upload.json()["id"])
        assert docx_job["status"] == "succeeded", docx_job
        assert "Hello from DOCX" in (kb_path / "converted.md").read_text()

        documents = client.get(f"/api/knowledge-bases/{kb['id']}/documents")
        assert documents.status_code == 200
        document = next(item for item in documents.json()["items"] if item["name"] == "guide.md")

        forced = client.post(
            f"/api/knowledge-bases/{kb['id']}/index",
            json={"force": True},
            headers=headers,
        )
        assert forced.status_code == 200
        forced_job = wait_for_job(client, forced.json()["id"])
        assert forced_job["status"] == "succeeded"

        deleted = client.delete(
            f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}",
            headers=headers,
        )
        assert deleted.status_code == 200
        delete_job = wait_for_job(client, deleted.json()["id"])
        assert delete_job["status"] == "succeeded"
        assert not (kb_path / "guide.md").exists()

        deleted_kb = client.delete(f"/api/knowledge-bases/{kb['id']}", headers=headers)
        assert deleted_kb.status_code == 200
        assert deleted_kb.json()["physicalDirectoryPreserved"] is True
        assert kb_path.is_dir()
        assert (kb_path / "converted.md").exists()
        assert client.get("/api/knowledge-bases").json()["items"] == []

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["agents", "add"] for command in state["commands"])
    memory_commands = [command for command in state["commands"] if command[:2] == ["memory", "index"]]
    assert any("--force" in command for command in memory_commands)


def test_existing_agent_path_can_be_inferred_without_creating_agent(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    inferred_path = tmp_path / "inferred-docs"
    state = json.loads(state_path.read_text())
    state["agents"][0]["memorySearch"] = {"extraPaths": [str(inferred_path)]}
    state_path.write_text(json.dumps(state))

    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        agents = client.get("/api/openclaw/agents")
        assert agents.status_code == 200
        assert agents.json()["items"][0]["extraPaths"] == [str(inferred_path)]
        assert agents.json()["items"][0]["pathSource"] == "agent"

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "自动目录", "agentId": "assistant"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["path"] == str(inferred_path)

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["agents", "add"] for command in state["commands"])


def test_agent_inherits_global_default_memory_directory(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    state = json.loads(state_path.read_text())
    workspace = Path(state["agents"][0]["workspace"])
    workspace.mkdir()
    inherited_path = workspace / "global-docs"
    state["defaults"] = {
        "memorySearch": {"extraPaths": ["global-docs"]},
    }
    state_path.write_text(json.dumps(state))

    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        agents = client.get("/api/openclaw/agents")
        assert agents.status_code == 200
        assert agents.json()["items"][0]["extraPaths"] == [str(inherited_path)]
        assert agents.json()["items"][0]["pathSource"] == "default"

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "全局目录", "agentId": "assistant"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["path"] == str(inherited_path)

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["config", "set"] for command in state["commands"])


def test_current_entries_and_memory_search_are_read_and_written(tmp_path, monkeypatch):
    workspace = tmp_path / "main-workspace"
    state = {
        "config": {
            "agents": {
                "defaults": {"workspace": str(workspace)},
                "entries": {
                    "main": {
                        "name": "Main",
                        "workspace": str(workspace),
                        "memory": {"search": {"extraPaths": ["agent-docs"]}},
                    }
                },
            },
            "memory": {"search": {"extraPaths": ["global-docs"]}},
        },
        "commands": [],
    }
    client, state_path = make_client(tmp_path, monkeypatch, state)
    managed_path = tmp_path / "managed-docs"

    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        agents = client.get("/api/openclaw/agents")
        assert agents.status_code == 200
        assert agents.json()["items"] == [
            {
                "id": "main",
                "name": "Main",
                "workspace": str(workspace),
                "extraPaths": [str(workspace / "global-docs"), str(workspace / "agent-docs")],
                "defaultPath": str(workspace / "wiki"),
                "pathSource": "agent",
                "used": False,
            }
        ]

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "当前配置", "agentId": "main", "path": str(managed_path)},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["path"] == str(managed_path)

    state = json.loads(state_path.read_text())
    entry = state["config"]["agents"]["entries"]["main"]
    assert entry["memory"]["search"]["extraPaths"] == ["agent-docs", str(managed_path)]
    set_commands = [command for command in state["commands"] if command[:2] == ["config", "set"]]
    assert set_commands[-1][2] == "agents.entries.main.memory.search.extraPaths"
    assert json.loads(set_commands[-1][3]) == ["agent-docs", str(managed_path)]


def test_current_global_memory_search_is_inherited_without_agent_write(tmp_path, monkeypatch):
    workspace = tmp_path / "main-workspace"
    state = {
        "config": {
            "agents": {
                "defaults": {"workspace": str(workspace)},
                "entries": {"main": {"name": "Main"}},
            },
            "memory": {"search": {"extraPaths": ["global-docs"]}},
        },
        "commands": [],
    }
    client, state_path = make_client(tmp_path, monkeypatch, state)
    workspace.mkdir()

    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        agents = client.get("/api/openclaw/agents")
        assert agents.status_code == 200
        assert agents.json()["items"][0]["extraPaths"] == [str(workspace / "global-docs")]
        assert agents.json()["items"][0]["pathSource"] == "default"

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "全局当前配置", "agentId": "main"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["path"] == str(workspace / "global-docs")

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["config", "set"] for command in state["commands"])


def test_current_agent_defaults_memory_search_is_inherited(tmp_path, monkeypatch):
    workspace = tmp_path / "main-workspace"
    state = {
        "config": {
            "agents": {
                "defaults": {
                    "workspace": str(workspace),
                    "memory": {"search": {"extraPaths": ["default-docs"]}},
                },
                "entries": {"main": {"name": "Main"}},
            }
        },
        "commands": [],
    }
    client, state_path = make_client(tmp_path, monkeypatch, state)
    workspace.mkdir()

    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        agents = client.get("/api/openclaw/agents")
        assert agents.status_code == 200
        assert agents.json()["items"][0]["extraPaths"] == [str(workspace / "default-docs")]
        assert agents.json()["items"][0]["pathSource"] == "default"

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "默认当前配置", "agentId": "main"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["path"] == str(workspace / "default-docs")

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["config", "set"] for command in state["commands"])


def test_unknown_agent_is_rejected_without_auto_creation(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    with client:
        csrf = login(client)
        response = client.post(
            "/api/knowledge-bases",
            json={"name": "Unknown", "agentId": "missing", "path": str(tmp_path / "docs")},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["agents", "add"] for command in state["commands"])


def test_upload_rejects_unsupported_extension(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "Docs", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        kb = created.json()
        response = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("bad.pdf", b"data", "application/pdf"))],
            headers=headers,
        )
        assert response.status_code == 400


def test_protected_document_endpoints_require_login(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/knowledge-bases/missing/documents").status_code == 401
        assert client.get("/api/knowledge-bases/missing/index/status").status_code == 401


def test_admin_can_change_password(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        csrf = login(client)
        response = client.post(
            "/api/auth/password",
            json={"currentPassword": "correct-horse-battery", "newPassword": "new-secure-password"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "correct-horse-battery"},
        ).status_code == 401
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "new-secure-password"},
        ).status_code == 200


def entries_state(tmp_path):
    main_ws = tmp_path / "main-ws"
    sales_ws = tmp_path / "sales-ws"
    main_ws.mkdir()
    sales_ws.mkdir()
    state = {
        "config": {
            "agents": {
                "defaults": {},
                "entries": {
                    "main": {"name": "Main", "workspace": str(main_ws)},
                    "sales": {"name": "Sales", "workspace": str(sales_ws)},
                },
            },
            "memory": {"search": {"extraPaths": ["wiki"]}},
        },
        "commands": [],
    }
    return state, main_ws, sales_ws


def test_public_knowledge_base_uses_global_paths_and_indexes_all_agents(tmp_path, monkeypatch):
    state, main_ws, sales_ws = entries_state(tmp_path)
    client, state_path = make_client(tmp_path, monkeypatch, state)
    public_path = tmp_path / "kb" / "public"
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        agents = client.get("/api/openclaw/agents")
        assert agents.status_code == 200
        assert agents.json()["publicDefaultPath"] == str(public_path)

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "公共库", "scope": "public"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        kb = created.json()
        assert kb["scope"] == "public"
        assert kb["agentId"] is None
        assert kb["path"] == str(public_path)
        assert kb["configChanged"] is True
        saved = json.loads(state_path.read_text())
        assert saved["config"]["memory"]["search"]["extraPaths"] == ["wiki", str(public_path)]

        # Public directories are not offered as agent candidates.
        items = {item["id"]: item for item in client.get("/api/openclaw/agents").json()["items"]}
        assert items["main"]["extraPaths"] == [str(main_ws / "wiki")]
        assert items["main"]["defaultPath"] == str(main_ws / "wiki")
        assert items["sales"]["used"] is False

        upload = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files=[("files", ("faq.md", b"# FAQ", "text/markdown"))],
            headers=headers,
        )
        assert upload.status_code == 200, upload.text
        assert wait_for_job(client, upload.json()["id"])["status"] == "succeeded"
        forced = client.post(f"/api/knowledge-bases/{kb['id']}/index", json={"force": True}, headers=headers)
        assert forced.status_code == 200, forced.text
        assert wait_for_job(client, forced.json()["id"])["status"] == "succeeded"

        deleted = client.delete(f"/api/knowledge-bases/{kb['id']}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["configChanged"] is True
        assert deleted.json()["scope"] == "public"

    saved = json.loads(state_path.read_text())
    index_commands = [command for command in saved["commands"] if command[:2] == ["memory", "index"]]
    # Without --agent, OpenClaw indexes every agent in agents.entries.
    assert index_commands == [
        ["memory", "index", "--verbose"],
        ["memory", "index", "--force", "--verbose"],
    ]
    assert saved["config"]["memory"]["search"]["extraPaths"] == ["wiki"]
    assert (public_path / "faq.md").is_file()


def test_agent_knowledge_base_defaults_to_workspace_wiki(tmp_path, monkeypatch):
    state, main_ws, sales_ws = entries_state(tmp_path)
    client, state_path = make_client(tmp_path, monkeypatch, state)
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        (tmp_path / "kb").mkdir()
        public = client.post(
            "/api/knowledge-bases",
            json={"name": "公共库", "scope": "public", "path": str(tmp_path / "kb" / "public")},
            headers=headers,
        )
        assert public.status_code == 200, public.text

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "销冠专属", "agentId": "sales"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        kb = created.json()
        assert kb["scope"] == "agent"
        assert kb["agentId"] == "sales"
        assert kb["path"] == str(sales_ws / "wiki")
        # The global relative "wiki" entry already covers <workspace>/wiki.
        assert kb["configChanged"] is False

        missing_agent = client.post(
            "/api/knowledge-bases",
            json={"name": "缺少 Agent", "path": str(tmp_path / "other")},
            headers=headers,
        )
        assert missing_agent.status_code == 400

    saved = json.loads(state_path.read_text())
    set_targets = [command[2] for command in saved["commands"] if command[:2] == ["config", "set"]]
    assert set_targets == ["memory.search.extraPaths"]


def test_legacy_database_gains_scope_column(tmp_path):
    import sqlite3

    from backend.db import Database

    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE knowledge_bases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL UNIQUE,
            agent_id TEXT NOT NULL UNIQUE,
            agent_workspace TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'provisioning',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO knowledge_bases VALUES ('kb1', 'Old', 'old', '/srv/old', 'main', '/ws', 'ready', NULL, 't', 't')"
    )
    connection.commit()
    connection.close()

    database = Database(db_path)
    database.initialize()
    database.initialize()  # idempotent
    row = database.fetchone("SELECT scope FROM knowledge_bases WHERE id = 'kb1'")
    assert row["scope"] == "agent"
    database.close()


def test_force_index_stops_gateway_before_indexing_and_restarts_after(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "Gateway 库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        kb = created.json()

        forced = client.post(f"/api/knowledge-bases/{kb['id']}/index", json={"force": True}, headers=headers)
        assert forced.status_code == 200, forced.text
        assert wait_for_job(client, forced.json()["id"])["status"] == "succeeded"

    saved = json.loads(state_path.read_text())
    relevant = [
        command
        for command in saved["commands"]
        if command[:2] in (["memory", "index"], ["gateway", "stop"], ["gateway", "start"])
    ]
    assert relevant == [
        ["gateway", "stop", "--force", "--json"],
        ["memory", "index", "--agent", "assistant", "--force", "--verbose"],
        ["gateway", "start", "--json"],
    ]
    assert saved["gateway_running"] is True


def test_force_index_restarts_gateway_when_index_fails(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_INDEX_FAIL", "1")
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "失败库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        kb = created.json()

        forced = client.post(f"/api/knowledge-bases/{kb['id']}/index", json={"force": True}, headers=headers)
        assert forced.status_code == 200, forced.text
        job = wait_for_job(client, forced.json()["id"])
        assert job["status"] == "failed"

    saved = json.loads(state_path.read_text())
    assert [command[:2] for command in saved["commands"] if command[:2] in (["gateway", "stop"], ["gateway", "start"])] == [
        ["gateway", "stop"],
        ["gateway", "start"],
    ]
    assert saved["gateway_running"] is True


def test_force_index_leaves_gateway_alone_when_stop_fails(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_GATEWAY_STOP_FAIL", "1")
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "停服失败库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        kb = created.json()

        forced = client.post(f"/api/knowledge-bases/{kb['id']}/index", json={"force": True}, headers=headers)
        assert forced.status_code == 200, forced.text
        assert wait_for_job(client, forced.json()["id"])["status"] == "failed"

    saved = json.loads(state_path.read_text())
    assert not any(command[:2] == ["gateway", "start"] for command in saved["commands"])
    assert not any(command[:2] == ["memory", "index"] for command in saved["commands"])


def test_force_index_treats_start_plugin_error_as_warning_when_gateway_serves(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_GATEWAY_START_FAIL", "running")
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "插件告警库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        kb = created.json()

        forced = client.post(f"/api/knowledge-bases/{kb['id']}/index", json={"force": True}, headers=headers)
        job = wait_for_job(client, forced.json()["id"])

    # The Gateway came up despite the plugin load error, so the job succeeds.
    assert job["status"] == "succeeded", job
    assert "Gateway restarted with warnings" in job["stderr"]
    assert "activated plugins reported load errors" in job["stderr"]


def test_force_index_fails_when_start_leaves_gateway_down(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_GATEWAY_START_FAIL", "1")
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "启动失败库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        kb = created.json()

        forced = client.post(f"/api/knowledge-bases/{kb['id']}/index", json={"force": True}, headers=headers)
        job = wait_for_job(client, forced.json()["id"])

    assert job["status"] == "failed", job
    saved = json.loads(state_path.read_text())
    assert saved.get("gateway_running") is not True


def _two_agent_state(tmp_path):
    return {
        "defaults": {},
        "agents": [
            {"id": "assistant", "name": "Assistant", "workspace": str(tmp_path / "agent-workspace")},
            {"id": "second", "name": "Second", "workspace": str(tmp_path / "second-workspace")},
        ],
        "commands": [],
    }


def _create_kb(client, headers, name, path, agent_id="assistant"):
    created = client.post(
        "/api/knowledge-bases",
        json={"name": name, "agentId": agent_id, "path": str(path)},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()


def test_documents_can_be_moved_to_another_knowledge_base(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch, _two_agent_state(tmp_path))
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        source = _create_kb(client, headers, "源库", tmp_path / "source-docs")
        target = _create_kb(client, headers, "目标库", tmp_path / "target-docs", agent_id="second")

        upload = client.post(
            f"/api/knowledge-bases/{source['id']}/documents",
            files=[("files", ("guide.md", b"# Guide", "text/markdown"))],
            headers=headers,
        )
        assert wait_for_job(client, upload.json()["id"])["status"] == "succeeded"
        document = client.get(f"/api/knowledge-bases/{source['id']}/documents").json()["items"][0]

        moved = client.post(
            f"/api/knowledge-bases/{source['id']}/documents/move",
            json={"targetKnowledgeBaseId": target["id"], "documentIds": [document["id"]]},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        assert wait_for_job(client, moved.json()["id"])["status"] == "succeeded"

        assert client.get(f"/api/knowledge-bases/{source['id']}/documents").json()["items"] == []
        target_items = client.get(f"/api/knowledge-bases/{target['id']}/documents").json()["items"]
        assert [item["name"] for item in target_items] == ["guide.md"]

    assert not (tmp_path / "source-docs" / "guide.md").exists()
    assert (tmp_path / "target-docs" / "guide.md").read_text() == "# Guide\n"


def test_move_rejects_name_collision_and_same_target(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, monkeypatch, _two_agent_state(tmp_path))
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        source = _create_kb(client, headers, "源库", tmp_path / "source-docs")
        target = _create_kb(client, headers, "目标库", tmp_path / "target-docs", agent_id="second")
        for kb, path in ((source, "source-docs"), (target, "target-docs")):
            upload = client.post(
                f"/api/knowledge-bases/{kb['id']}/documents",
                files=[("files", ("guide.md", b"# Guide", "text/markdown"))],
                headers=headers,
            )
            assert wait_for_job(client, upload.json()["id"])["status"] == "succeeded"

        document = client.get(f"/api/knowledge-bases/{source['id']}/documents").json()["items"][0]

        same = client.post(
            f"/api/knowledge-bases/{source['id']}/documents/move",
            json={"targetKnowledgeBaseId": source["id"], "documentIds": [document["id"]]},
            headers=headers,
        )
        assert same.status_code == 400
        assert (tmp_path / "source-docs" / "guide.md").exists()

        collision = client.post(
            f"/api/knowledge-bases/{source['id']}/documents/move",
            json={"targetKnowledgeBaseId": target["id"], "documentIds": [document["id"]]},
            headers=headers,
        )
        assert collision.status_code == 409
        assert "guide.md" in collision.json()["detail"]
        assert (tmp_path / "source-docs" / "guide.md").exists()
        assert (tmp_path / "target-docs" / "guide.md").read_text() == "# Guide\n"


def test_incremental_index_only_touches_its_own_agent(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "单 Agent 库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        kb = created.json()

        indexed = client.post(
            f"/api/knowledge-bases/{kb['id']}/index", json={"force": False}, headers=headers
        )
        assert indexed.status_code == 200, indexed.text
        job = wait_for_job(client, indexed.json()["id"])
        assert job["status"] == "succeeded", job

    saved = json.loads(state_path.read_text())
    relevant = [
        command
        for command in saved["commands"]
        if command[:2] in (["memory", "index"], ["gateway", "stop"], ["gateway", "start"])
    ]
    # OpenClaw 11.x refuses memory index while the Gateway owns the state dir,
    # so an incremental run stops and restarts it too.
    assert relevant == [
        ["gateway", "stop", "--force", "--json"],
        ["memory", "index", "--agent", "assistant", "--verbose"],
        ["gateway", "start", "--json"],
    ]


def test_index_does_not_start_a_gateway_that_was_never_loaded(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_GATEWAY_NOT_LOADED", "1")
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/knowledge-bases",
            json={"name": "无服务库", "agentId": "assistant", "path": str(tmp_path / "docs")},
            headers=headers,
        )
        kb = created.json()

        indexed = client.post(
            f"/api/knowledge-bases/{kb['id']}/index", json={"force": False}, headers=headers
        )
        job = wait_for_job(client, indexed.json()["id"])
        assert job["status"] == "succeeded", job

    saved = json.loads(state_path.read_text())
    assert not any(command[:2] == ["gateway", "start"] for command in saved["commands"])


def test_move_stops_gateway_once_and_indexes_both_agents(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch, _two_agent_state(tmp_path))
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        source = _create_kb(client, headers, "源库", tmp_path / "source-docs")
        target = _create_kb(client, headers, "目标库", tmp_path / "target-docs", agent_id="second")
        upload = client.post(
            f"/api/knowledge-bases/{source['id']}/documents",
            files=[("files", ("guide.md", b"# Guide", "text/markdown"))],
            headers=headers,
        )
        assert wait_for_job(client, upload.json()["id"])["status"] == "succeeded"
        document = client.get(f"/api/knowledge-bases/{source['id']}/documents").json()["items"][0]

        # Only commands from here on matter; the upload above already indexed.
        before = len(json.loads(state_path.read_text())["commands"])
        moved = client.post(
            f"/api/knowledge-bases/{source['id']}/documents/move",
            json={"targetKnowledgeBaseId": target["id"], "documentIds": [document["id"]]},
            headers=headers,
        )
        job = wait_for_job(client, moved.json()["id"])
        assert job["status"] == "succeeded", job

    commands = json.loads(state_path.read_text())["commands"][before:]
    relevant = [
        command
        for command in commands
        if command[:2] in (["memory", "index"], ["gateway", "stop"], ["gateway", "start"])
    ]
    assert relevant == [
        ["gateway", "stop", "--force", "--json"],
        ["memory", "index", "--agent", "assistant", "--verbose"],
        ["memory", "index", "--agent", "second", "--verbose"],
        ["gateway", "start", "--json"],
    ]
    assert "[源库]" in job["stdout"] and "[目标库]" in job["stdout"]


def test_move_keeps_indexing_and_restarts_gateway_when_one_side_fails(tmp_path, monkeypatch):
    client, state_path = make_client(tmp_path, monkeypatch, _two_agent_state(tmp_path))
    with client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        source = _create_kb(client, headers, "源库", tmp_path / "source-docs")
        target = _create_kb(client, headers, "目标库", tmp_path / "target-docs", agent_id="second")
        upload = client.post(
            f"/api/knowledge-bases/{source['id']}/documents",
            files=[("files", ("guide.md", b"# Guide", "text/markdown"))],
            headers=headers,
        )
        assert wait_for_job(client, upload.json()["id"])["status"] == "succeeded"
        document = client.get(f"/api/knowledge-bases/{source['id']}/documents").json()["items"][0]

        before = len(json.loads(state_path.read_text())["commands"])
        monkeypatch.setenv("FAKE_INDEX_FAIL", "1")
        moved = client.post(
            f"/api/knowledge-bases/{source['id']}/documents/move",
            json={"targetKnowledgeBaseId": target["id"], "documentIds": [document["id"]]},
            headers=headers,
        )
        job = wait_for_job(client, moved.json()["id"])
        assert job["status"] == "failed", job
        assert "源库" in job["error"]

    commands = json.loads(state_path.read_text())["commands"][before:]
    pairs = [command[:2] for command in commands if command[:2] in (["gateway", "stop"], ["gateway", "start"])]
    assert pairs == [["gateway", "stop"], ["gateway", "start"]]
    # The file still moved; only the reindex failed.
    assert not (tmp_path / "source-docs" / "guide.md").exists()
    assert (tmp_path / "target-docs" / "guide.md").exists()
