from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from toolyard.cli import main
from toolyard.docker_driver import MockDockerDriver
from toolyard.secrets import MockSecretResolver


def test_validate_and_secrets_commands(tmp_path, capsys):
    tools = tmp_path / "tools"
    folder = tools / "hello-rest"
    folder.mkdir(parents=True)
    (folder / "toolyard.yaml").write_text(
        """
id: hello-rest
type: rest
entrypoint:
  image: hello:latest
  port: 5000
secrets:
  - { name: api_key, field: API_KEY }
""",
        encoding="utf-8",
    )

    with mock.patch.dict(os.environ, {"TOOLYARD_TOOLS_DIR": str(tools)}):
        main(["validate", str(folder)])
        main(["secrets", "hello-rest"])

    output = capsys.readouterr().out
    assert "ok (hello-rest)" in output
    assert "ToolServer/hello-rest/API_KEY" in output


def test_up_and_ls_json_with_injected_mocks(tmp_path, capsys):
    tools = tmp_path / "tools"
    folder = tools / "hello-rest"
    folder.mkdir(parents=True)
    (folder / "toolyard.yaml").write_text(
        """
id: hello-rest
type: rest
entrypoint:
  image: hello:latest
  port: 5000
secrets:
  - { name: api_key, field: API_KEY }
""",
        encoding="utf-8",
    )
    driver = MockDockerDriver()
    resolver = MockSecretResolver({("ToolServer", "hello-rest", "API_KEY"): "secret"})

    env = {"TOOLYARD_TOOLS_DIR": str(tools)}
    with mock.patch.dict(os.environ, env):
        main(
            ["up", "hello-rest"],
            driver_factory=lambda: driver,
            resolver_factory=lambda config, descs: resolver,
        )
        main(["ls", "--json"], driver_factory=lambda: driver)

    output = capsys.readouterr().out
    assert "hello-rest: started" in output
    data = json.loads(output[output.index("{"):])
    assert data["tools"][0]["id"] == "hello-rest"
    assert data["tools"][0]["running"] is True


def test_cli_errors_exit_nonzero(tmp_path, capsys):
    with mock.patch.dict(os.environ, {"TOOLYARD_TOOLS_DIR": str(tmp_path / "tools")}):
        with pytest.raises(SystemExit) as exc:
            main(["secrets", "missing"], driver_factory=MockDockerDriver)
    assert exc.value.code == 1
    assert "unknown tool" in capsys.readouterr().err
