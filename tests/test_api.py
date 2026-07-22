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


def make_client(tmp_path, monkeypatch):
    state_path = tmp_path / "openclaw-state.json"
    state_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "assistant",
                        "name": "Assistant",
                        "workspace": str(tmp_path / "agent-workspace"),
                    }
                ],
                "commands": [],
            }
        )
    )
    monkeypatch.setenv("FAKE_OPENCLAW_STATE", str(state_path))
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "APP_DATA_DIR": str(tmp_path / "runtime"),
            "ADMIN_USERNAME": "admin",
            "ADMIN_INITIAL_PASSWORD": "correct-horse-battery",
            "OPENCLAW_BIN": str(FIXTURE),
            "APP_START_WORKER": "true",
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

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "自动目录", "agentId": "assistant"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["path"] == str(inferred_path)

    state = json.loads(state_path.read_text())
    assert not any(command[:2] == ["agents", "add"] for command in state["commands"])


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
