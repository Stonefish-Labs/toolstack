from __future__ import annotations

from pathlib import Path
import os

import pytest

from toolyard.config import Config
from toolyard.docker_driver import MockDockerDriver
from toolyard.lifecycle import container_name, down, list_tools, up
from toolyard.schema import load_descriptor, validate_descriptor_dict
from toolyard.secrets import MockSecretResolver


def test_up_builds_resolves_and_injects_secrets_without_host_files(tmp_path):
    folder = tmp_path / "tools" / "hello-rest"
    folder.mkdir(parents=True)
    (folder / "toolyard.yaml").write_text(
        """
id: hello-rest
type: rest
entrypoint:
  build: .
  port: 5000
secrets:
  - { name: api_key, field: API_KEY }
""",
        encoding="utf-8",
    )
    desc = load_descriptor(folder)
    config = Config(tools_dir=tmp_path / "tools")
    driver = MockDockerDriver()
    image = "mock-image:toolyard-hello-rest:latest"
    driver.image_commands[image] = ["uvicorn", "app:app", "--port", "5000"]
    resolver = MockSecretResolver({("ToolServer", "hello-rest", "API_KEY"): "secret"})

    result = up(descriptor=desc, config=config, driver=driver, resolver=resolver)

    assert result.tool_id == "hello-rest"
    assert driver.builds[0]["tag"] == "toolyard-hello-rest:latest"
    record = driver.inspect(container_name("hello-rest"))
    assert record["port_mapping"] == (5000, 5000)
    assert len(record["volumes"]) == 1
    assert record["volumes"][0][1:] == ("/toolyard/wait-for-secrets", "ro")
    assert record["volumes"][0][0].endswith("wait-for-secrets.sh")
    assert record["tmpfs"] == ["/run/secrets:rw,noexec,nosuid,nodev,size=1m"]
    assert record["entrypoint"] == "/bin/sh"
    assert record["command"] == [
        "/toolyard/wait-for-secrets", "uvicorn", "app:app", "--port", "5000",
    ]
    assert record["user"] == "10000:10000"
    assert record["cap_drop_all"] is True
    assert record["read_only"] is True
    assert record["env"] == {}
    assert not (tmp_path / "secrets" / "hello-rest" / "api_key").exists()
    assert driver.archives == [{
        "name": "toolyard-hello-rest",
        "dest_path": "/run/secrets",
        "files": {"api_key": "secret", ".ready": "1"},
        "uid": 10000,
        "gid": 10000,
    }]


def test_up_without_secrets_does_not_need_resolver_or_secret_mount(tmp_path):
    desc = validate_descriptor_dict({
        "id": "plain",
        "type": "rest",
        "entrypoint": {"image": "plain:latest", "port": 5050},
    })
    driver = MockDockerDriver()
    up(
        descriptor=desc,
        config=Config(),
        driver=driver,
        resolver=None,
    )
    record = driver.inspect(container_name("plain"))
    assert record["volumes"] == []
    assert record["tmpfs"] == []
    assert driver.archives == []
    assert not (tmp_path / "secrets" / "plain").exists()


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"type": "mcp-stdio"}, "mcp-stdio"),
        ({"network": "isolated"}, "networks"),
        ({"volumes": [{"host": "/x", "container": "/x"}]}, "volumes"),
    ],
)
def test_deferred_runtime_features_fail_clearly(tmp_path, patch, message):
    data = {"id": "x", "type": "rest", "entrypoint": {"image": "x", "port": 5000}}
    data.update(patch)
    desc = validate_descriptor_dict(data)
    with pytest.raises(NotImplementedError, match=message):
        up(descriptor=desc, config=Config(), driver=MockDockerDriver())


def test_writable_secrets_require_a_write_proxy_socket(tmp_path):
    desc = validate_descriptor_dict({
        "id": "oauth",
        "type": "rest",
        "entrypoint": {"image": "oauth:latest", "port": 5000, "command": ["run-oauth"]},
        "secrets": [{"name": "refresh_token", "field": "REFRESH_TOKEN", "writable": True}],
    })
    resolver = MockSecretResolver({("ToolServer", "oauth", "REFRESH_TOKEN"): "r1"})
    with pytest.raises(ValueError, match="writable secrets"):
        up(
            descriptor=desc,
            config=Config(),
            driver=MockDockerDriver(),
            resolver=resolver,
        )


def test_writable_secrets_mount_only_the_toolyard_socket_dir(tmp_path):
    desc = validate_descriptor_dict({
        "id": "oauth",
        "type": "rest",
        "entrypoint": {"image": "oauth:latest", "port": 5000, "command": ["run-oauth"]},
        "secrets": [{"name": "refresh_token", "field": "REFRESH_TOKEN", "writable": True}],
    })
    resolver = MockSecretResolver({("ToolServer", "oauth", "REFRESH_TOKEN"): "r1"})
    socket_dir = tmp_path / "runtime" / "sockets" / "oauth"
    driver = MockDockerDriver()

    up(
        descriptor=desc,
        config=Config(),
        driver=driver,
        resolver=resolver,
        write_socket_dir=socket_dir,
    )

    record = driver.inspect(container_name("oauth"))
    assert (str(socket_dir), "/run/toolyard", "ro") in record["volumes"]
    assert record["group_add"] == [str(os.getgid())]
    assert driver.archives[0]["files"] == {"refresh_token": "r1", ".ready": "1"}


def test_down_is_idempotent():
    driver = MockDockerDriver()
    down(tool_id="missing", driver=driver)


def test_list_tools_uses_registry_and_driver(tmp_path):
    tools = tmp_path / "tools"
    folder = tools / "plain"
    folder.mkdir(parents=True)
    (folder / "toolyard.yaml").write_text(
        """
id: plain
type: rest
entrypoint:
  image: plain:latest
  port: 5050
""",
        encoding="utf-8",
    )
    config = Config(tools_dir=tools)
    driver = MockDockerDriver()
    desc = load_descriptor(folder)
    up(descriptor=desc, config=config, driver=driver)

    statuses = list_tools(config=config, driver=driver)
    assert statuses[0].id == "plain"
    assert statuses[0].running is True
    assert statuses[0].host_port == 5050
