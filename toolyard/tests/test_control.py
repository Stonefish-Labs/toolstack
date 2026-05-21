from __future__ import annotations

import httpx

from toolyard.config import Config
from toolyard.control import ToolyardControlServer
from toolyard.daemon import ToolyardDaemon
from toolyard.docker_driver import MockDockerDriver


def test_control_socket_lists_and_controls_tools(tmp_path):
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
    config = Config(
        tools_dir=tools,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        control_socket=tmp_path / "run" / "control.sock",
    )
    daemon = ToolyardDaemon(config=config, driver=MockDockerDriver())
    server = ToolyardControlServer(socket_path=config.control_socket, daemon=daemon)
    server.start()
    try:
        transport = httpx.HTTPTransport(uds=str(config.control_socket))
        with httpx.Client(transport=transport, base_url="http://toolyard") as client:
            response = client.get("/v1/tools")
            assert response.status_code == 200
            assert response.json()["tools"][0]["running"] is False

            response = client.post("/v1/tools/plain/start")
            assert response.status_code == 200
            assert response.json()["tool"]["running"] is True

            response = client.post("/v1/tools/plain/restart")
            assert response.status_code == 200
            assert response.json()["tool"]["running"] is True

            response = client.post("/v1/tools/plain/stop")
            assert response.status_code == 200
            assert response.json()["tool"]["running"] is False
    finally:
        server.stop()
