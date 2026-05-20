from __future__ import annotations

import shutil

import pytest

from toolyard.docker_driver import CLIDockerDriver, MockDockerDriver


def test_mock_driver_records_lifecycle(tmp_path):
    driver = MockDockerDriver()
    image = driver.build(context=tmp_path, tag="toolyard-x:latest")
    cid = driver.run(
        name="toolyard-x",
        image=image,
        port_mapping=(5000, 5000),
        volumes=[],
        env={},
    )
    assert cid == "mock-container-1"
    assert driver.ps()[0]["Names"] == "toolyard-x"
    driver.stop("toolyard-x")
    assert driver.inspect("toolyard-x")["State"] == "exited"
    driver.remove("toolyard-x")
    assert driver.ps() == []


def test_cli_driver_logs_combines_stdout_and_stderr(monkeypatch):
    class Completed:
        stdout = "out"
        stderr = "err"

    driver = CLIDockerDriver()
    monkeypatch.setattr(driver, "_run", lambda *a, **k: Completed())
    assert driver.logs("toolyard-x") == "outerr"


@pytest.mark.docker
def test_cli_driver_available_when_docker_installed():
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    assert isinstance(CLIDockerDriver().ps(), list)
