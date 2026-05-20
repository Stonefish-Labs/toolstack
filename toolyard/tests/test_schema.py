from __future__ import annotations

import pytest

from toolyard.schema import validate_descriptor_dict


def valid_data(**overrides):
    data = {
        "id": "hello-rest",
        "type": "rest",
        "entrypoint": {"build": ".", "port": 5000},
        "secrets": [{"name": "api_key", "field": "API_KEY"}],
        "operations": [{"op": "greet", "risk": "read"}],
    }
    data.update(overrides)
    return data


def test_valid_descriptor_defaults_secret_item():
    desc = validate_descriptor_dict(valid_data())
    assert desc.id == "hello-rest"
    assert desc.secrets[0].vault == "ToolServer"
    assert desc.secrets[0].item == "hello-rest"


def test_writable_secret_is_schema_valid():
    desc = validate_descriptor_dict(valid_data(
        secrets=[{"name": "refresh_token", "field": "REFRESH_TOKEN", "writable": True}],
    ))
    assert desc.has_writable_secrets is True


def test_mcp_stdio_is_schema_valid():
    desc = validate_descriptor_dict(valid_data(type="mcp-stdio"))
    assert desc.type == "mcp-stdio"


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "Hello"},
        {"entrypoint": {"build": ".", "image": "example:latest", "port": 1}},
        {"entrypoint": {"port": 1}},
        {"secrets": [{"name": "../bad", "field": "X"}]},
        {"secrets": [{"name": "_connect_token", "field": "X"}]},
        {"secrets": [{"name": ".ready", "field": "X"}]},
    ],
)
def test_invalid_descriptors_fail(patch):
    with pytest.raises(ValueError):
        validate_descriptor_dict(valid_data(**patch))
