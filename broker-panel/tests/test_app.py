from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from broker_panel.app import create_app
from broker_panel.config import Config
from broker_panel.security import hash_password


class FakeBroker:
    def __init__(self):
        self.created_callers: list[str] = []
        self.saved_policies: list[tuple[str, dict]] = []
        self.reloads = 0
        self.tools = {
            "music": {
                "id": "music",
                "description": "Music controls",
                "operations": [
                    {"op": "get_status", "risk": "read", "description": "Read playback"},
                    {"op": "play_item", "risk": "write", "description": "Start a track"},
                ],
            }
        }
        self.policy = {
            "caller": "agent.kira",
            "tools": {
                "music": {
                    "operations": {
                        "get_status": "allow",
                        "play_item": "review",
                    }
                }
            },
            "broker_ops": ["broker.audit"],
            "auto_grant_ttl_seconds": 3600,
        }
        self.toolyard_actions: list[tuple[str, str]] = []

    async def get_tools(self):
        return self.tools

    async def reload_tools(self):
        self.reloads += 1
        self.tools["notes"] = {
            "id": "notes",
            "description": "Note lookup",
            "operations": [
                {"op": "list_notes", "risk": "read", "description": "List notes"},
            ],
        }
        return {"reloaded": True, "tool_count": len(self.tools)}

    async def get_toolyard_tools(self):
        return [
            {
                "id": "music",
                "enabled": True,
                "running": True,
                "healthy": None,
                "host_port": 5200,
                "container_id": "abc123",
                "image_id": "music:latest",
            }
        ]

    async def control_toolyard_tool(self, tool_id: str, action: str):
        self.toolyard_actions.append((tool_id, action))
        return {"action": action, "tool": {"id": tool_id, "running": action != "stop"}}

    async def get_callers(self):
        return [{"id": 1, "name": "agent.kira", "revoked_at": None}]

    async def create_caller(self, name: str, policy=None):
        self.created_callers.append(name)
        return {"caller": {"name": name}, "token": "raw-token", "hash_prefix": "12345678"}

    async def revoke_caller(self, name: str):
        return {"revoked": True}

    async def get_tokens(self):
        return [{"hash_prefix": "12345678", "caller_name": "agent.kira", "revoked_at": None}]

    async def revoke_token(self, hash_prefix: str):
        return {"revoked": 1}

    async def refresh_caller_token(self, caller: str):
        return {"caller": {"name": caller}, "token": "fresh-token", "hash_prefix": "abcdef12", "revoked": 1}

    async def get_caller_policy(self, caller: str):
        data = dict(self.policy)
        data["caller"] = caller
        return data

    async def put_caller_policy(self, caller: str, body: dict):
        self.saved_policies.append((caller, body))
        return {"caller": caller, **body}

    async def get_requests(self):
        return [{"id": 1, "caller": "agent.kira", "tool": "music", "op": "play_item", "status": "completed"}]

    async def get_audit(self):
        return [{"id": 1, "kind": "request.completed", "tool": "music", "op": "play_item"}]


def _config(tmp_path: Path) -> Config:
    password_file = tmp_path / "password.hash"
    password_file.write_text(hash_password("secret"), encoding="utf-8")
    session_file = tmp_path / "session.key"
    session_file.write_text("session-secret", encoding="utf-8")
    token_file = tmp_path / "broker.token"
    token_file.write_text("broker-token", encoding="utf-8")
    return Config(
        broker_token_file=token_file,
        password_hash_file=password_file,
        session_secret_file=session_file,
    )


def test_dashboard_requires_login(tmp_path):
    app = create_app(_config(tmp_path), broker=FakeBroker())
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_and_dashboard_render(tmp_path):
    app = create_app(_config(tmp_path), broker=FakeBroker())
    with TestClient(app) as client:
        response = client.post(
            "/login",
            content="username=admin&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        response = client.get("/")
    assert response.status_code == 200
    assert "agent.kira" in response.text
    assert "music" in response.text
    assert "Toolyard" in response.text
    assert "abc123" in response.text


def test_create_caller_shows_one_time_token(tmp_path):
    broker = FakeBroker()
    app = create_app(_config(tmp_path), broker=broker)
    with TestClient(app) as client:
        client.post(
            "/login",
            content="username=admin&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = client.post(
            "/callers",
            content="name=agent.new",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    assert "raw-token" in response.text
    assert broker.created_callers == ["agent.new"]


def test_reload_tools_refreshes_dashboard(tmp_path):
    broker = FakeBroker()
    app = create_app(_config(tmp_path), broker=broker)
    with TestClient(app) as client:
        client.post(
            "/login",
            content="username=admin&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = client.post(
            "/tools/reload",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    assert broker.reloads == 1
    assert "Reloaded tool registry: 2 tool(s)" in response.text
    assert "notes" in response.text


def test_toolyard_action_posts_to_broker(tmp_path):
    broker = FakeBroker()
    app = create_app(_config(tmp_path), broker=broker)
    with TestClient(app) as client:
        client.post(
            "/login",
            content="username=admin&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = client.post(
            "/toolyard/tools/music/restart",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    assert broker.toolyard_actions == [("music", "restart")]
    assert "Toolyard restart requested for music" in response.text


def test_caller_policy_save_posts_per_operation_payload(tmp_path):
    broker = FakeBroker()
    app = create_app(_config(tmp_path), broker=broker)
    with TestClient(app) as client:
        client.post(
            "/login",
            content="username=admin&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = client.post(
            "/callers/agent.kira/policy",
            content=(
                "auto_grant_ttl_seconds=120&"
                "broker_ops=broker.audit%0A&"
                "op__music__get_status=allow&"
                "op__music__play_item=deny"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert broker.saved_policies == [
        (
            "agent.kira",
            {
                "tools": {
                    "music": {
                        "operations": {
                            "get_status": "allow",
                            "play_item": "deny",
                        }
                    }
                },
                "broker_ops": ["broker.audit"],
                "auto_grant_ttl_seconds": 120,
            },
        )
    ]
