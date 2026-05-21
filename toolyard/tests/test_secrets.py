from __future__ import annotations

import json

import httpx
import pytest

from toolyard.schema import validate_descriptor_dict
from toolyard.secrets import (
    InfisicalSecretResolver,
    MockSecretResolver,
    load_infisical_credentials,
    resolve_secrets,
)


def test_resolve_secrets_returns_in_memory_values_without_writing(tmp_path):
    desc = validate_descriptor_dict({
        "id": "hello-rest",
        "type": "rest",
        "entrypoint": {"image": "hello:latest", "port": 5000},
        "secrets": [{"name": "api_key", "field": "API_KEY"}],
    })
    resolver = MockSecretResolver({("ToolServer", "hello-rest", "API_KEY"): "shh"})

    values = resolve_secrets(descriptor=desc, resolver=resolver)

    assert values == {"api_key": "shh"}
    assert list(tmp_path.iterdir()) == []


def test_writable_secrets_are_resolved_like_read_secrets():
    desc = validate_descriptor_dict({
        "id": "oauth",
        "type": "rest",
        "entrypoint": {"image": "oauth:latest", "port": 5000},
        "secrets": [{"name": "refresh_token", "field": "REFRESH_TOKEN", "writable": True}],
    })
    values = resolve_secrets(
        descriptor=desc,
        resolver=MockSecretResolver({("ToolServer", "oauth", "REFRESH_TOKEN"): "r"}),
    )
    assert values == {"refresh_token": "r"}


def test_load_infisical_credentials_accepts_env_and_colon_syntax(tmp_path):
    path = tmp_path / "demo-tool.env"
    path.write_text(
        """
client_id: demo-id
INFISICAL_CLIENT_SECRET="demo-secret"
""",
        encoding="utf-8",
    )

    creds = load_infisical_credentials(path)

    assert creds.client_id == "demo-id"
    assert creds.client_secret == "demo-secret"
    assert creds.source == path


def test_infisical_resolver_reads_secret_from_item_path_with_item_credentials(tmp_path):
    credentials_dir = tmp_path / "infisical"
    credentials_dir.mkdir()
    (credentials_dir / "demo-tool.env").write_text(
        "INFISICAL_CLIENT_ID=demo-client\n"
        "INFISICAL_CLIENT_SECRET=demo-secret\n",
        encoding="utf-8",
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/auth/universal-auth/login":
            assert json.loads(request.content) == {
                "clientId": "demo-client",
                "clientSecret": "demo-secret",
            }
            return httpx.Response(200, json={"accessToken": "token", "expiresIn": 3600})
        if request.url.path == "/api/v1/projects":
            assert request.headers["Authorization"] == "Bearer token"
            return httpx.Response(200, json={"projects": [{"id": "project-id", "name": "ToolServer"}]})
        if request.url.path == "/api/v4/secrets":
            params = dict(request.url.params)
            assert params["projectId"] == "project-id"
            assert params["environment"] == "prod"
            assert params["secretPath"] == "/demo-tool"
            return httpx.Response(
                200,
                json={"secrets": [{"secretKey": "client_id", "secretValue": "resolved-id"}]},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    resolver = InfisicalSecretResolver(
        host="https://infisical.example",
        credentials_dir=credentials_dir,
        environment="prod",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert resolver.resolve("ToolServer", "demo-tool", "client_id") == "resolved-id"
    assert [request.url.path for request in seen] == [
        "/api/v1/auth/universal-auth/login",
        "/api/v1/projects",
        "/api/v4/secrets",
    ]


def test_infisical_writer_patches_secret_in_item_path(tmp_path):
    credentials_dir = tmp_path / "infisical"
    credentials_dir.mkdir()
    (credentials_dir / "demo-tool.env").write_text(
        "INFISICAL_CLIENT_ID=demo-client\n"
        "INFISICAL_CLIENT_SECRET=demo-secret\n",
        encoding="utf-8",
    )
    patch_payload: dict | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_payload
        if request.url.path == "/api/v1/auth/universal-auth/login":
            return httpx.Response(200, json={"accessToken": "token", "expiresIn": 3600})
        if request.url.path == "/api/v1/projects":
            return httpx.Response(200, json={"projects": [{"id": "project-id", "slug": "ToolServer"}]})
        if request.method == "PATCH" and request.url.path == "/api/v4/secrets/OAUTH_TOKEN":
            patch_payload = json.loads(request.content)
            return httpx.Response(200, json={"secret": {"secretKey": "OAUTH_TOKEN"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    resolver = InfisicalSecretResolver(
        host="https://infisical.example",
        credentials_dir=credentials_dir,
        environment="prod",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    resolver.update("ToolServer", "demo-tool", "OAUTH_TOKEN", "new-token")

    assert patch_payload == {
        "projectId": "project-id",
        "environment": "prod",
        "secretValue": "new-token",
        "secretPath": "/demo-tool",
        "type": "shared",
    }


def test_infisical_resolver_reports_missing_item_credentials(tmp_path):
    resolver = InfisicalSecretResolver(
        host="https://infisical.example",
        credentials_dir=tmp_path,
        environment="prod",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )

    with pytest.raises(FileNotFoundError, match="hello-rest.env"):
        resolver.resolve("ToolServer", "hello-rest", "API_KEY")
