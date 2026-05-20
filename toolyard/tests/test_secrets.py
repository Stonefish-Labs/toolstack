from __future__ import annotations

from toolyard.schema import validate_descriptor_dict
from toolyard.secrets import MockSecretResolver, resolve_secrets


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
