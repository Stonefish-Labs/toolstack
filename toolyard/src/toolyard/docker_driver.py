"""Docker driver seam."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol


class DockerDriver(Protocol):
    def build(self, *, context: Path, tag: str) -> str: ...
    def pull(self, image: str) -> str: ...
    def image_command(self, image: str) -> list[str]: ...
    def run(
        self, *, name: str, image: str, port_mapping: tuple[int, int],
        bind_addr: str = "127.0.0.1", volumes: list[tuple[str, str, str]],
        env: dict[str, str], user: str = "10000:10000",
        cap_drop_all: bool = True, read_only: bool = False,
        tmpfs: list[str] | None = None, entrypoint: str | None = None,
        command: list[str] | None = None, group_add: list[str] | None = None,
    ) -> str: ...
    def copy_archive(self, name: str, dest_path: str, files: dict[str, str], *, uid: int, gid: int) -> None: ...
    def stop(self, name: str) -> None: ...
    def remove(self, name: str) -> None: ...
    def logs(self, name: str, tail: int | None = None, follow: bool = False) -> str: ...
    def inspect(self, name: str) -> dict: ...
    def ps(self, name_prefix: str = "toolyard-") -> list[dict]: ...


class CLIDockerDriver:
    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, text=True, capture_output=True, check=check)

    def build(self, *, context: Path, tag: str) -> str:
        cp = self._run(["docker", "build", "-q", "-t", tag, str(context)])
        return cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else tag

    def pull(self, image: str) -> str:
        self._run(["docker", "pull", image])
        return image

    def image_command(self, image: str) -> list[str]:
        cp = self._run(["docker", "image", "inspect", image, "--format", "{{json .Config}}"])
        config = json.loads(cp.stdout)
        entrypoint = config.get("Entrypoint") or []
        cmd = config.get("Cmd") or []
        if isinstance(entrypoint, str):
            entrypoint = [entrypoint]
        if isinstance(cmd, str):
            cmd = [cmd]
        return [*entrypoint, *cmd]

    def run(self, **kwargs) -> str:
        host_port, container_port = kwargs["port_mapping"]
        args = [
            "docker", "run", "-d", "--name", kwargs["name"],
            "-p", f"{kwargs.get('bind_addr', '127.0.0.1')}:{host_port}:{container_port}",
            "--user", kwargs.get("user", "10000:10000"),
        ]
        if kwargs.get("cap_drop_all", True):
            args += ["--cap-drop", "ALL"]
        if kwargs.get("read_only", False):
            args.append("--read-only")
        for group in kwargs.get("group_add") or []:
            args += ["--group-add", str(group)]
        if kwargs.get("entrypoint"):
            args += ["--entrypoint", kwargs["entrypoint"]]
        for spec in kwargs.get("tmpfs") or []:
            args += ["--tmpfs", spec]
        for host, container, mode in kwargs["volumes"]:
            args += ["-v", f"{host}:{container}:{mode}"]
        for key, value in sorted(kwargs["env"].items()):
            args += ["-e", f"{key}={value}"]
        args.append(kwargs["image"])
        args += kwargs.get("command") or []
        return self._run(args).stdout.strip()

    def copy_archive(self, name: str, dest_path: str, files: dict[str, str], *, uid: int, gid: int) -> None:
        for file_name, value in files.items():
            mode = "0444" if file_name == ".ready" else "0400"
            subprocess.run(
                [
                    "docker", "exec", "-i", "--user", f"{uid}:{gid}", name,
                    "/bin/sh", "-c",
                    "set -eu; dest=\"$1/$2\"; umask 077; cat > \"$dest\"; chmod \"$3\" \"$dest\"",
                    "sh", dest_path, file_name, mode,
                ],
                input=value.encode("utf-8"),
                capture_output=True,
                check=True,
            )

    def stop(self, name: str) -> None:
        self._run(["docker", "stop", name], check=False)

    def remove(self, name: str) -> None:
        self._run(["docker", "rm", name], check=False)

    def logs(self, name: str, tail: int | None = None, follow: bool = False) -> str:
        args = ["docker", "logs"]
        if follow:
            args.append("--follow")
        if tail is not None:
            args += ["--tail", str(tail)]
        args.append(name)
        cp = self._run(args, check=False)
        return cp.stdout + cp.stderr

    def inspect(self, name: str) -> dict:
        cp = self._run(["docker", "inspect", name], check=False)
        if cp.returncode != 0:
            return {}
        data = json.loads(cp.stdout or "[]")
        return data[0] if data else {}

    def ps(self, name_prefix: str = "toolyard-") -> list[dict]:
        cp = self._run(
            ["docker", "ps", "-a", "--filter", f"name={name_prefix}", "--format", "{{json .}}"],
            check=False,
        )
        return [json.loads(line) for line in cp.stdout.splitlines() if line.strip()]


class MockDockerDriver:
    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.builds: list[dict] = []
        self.pulls: list[str] = []
        self.archives: list[dict] = []
        self.image_commands: dict[str, list[str]] = {}
        self.next_id = 1

    def build(self, *, context: Path, tag: str) -> str:
        self.builds.append({"context": context, "tag": tag})
        return f"mock-image:{tag}"

    def pull(self, image: str) -> str:
        self.pulls.append(image)
        return image

    def image_command(self, image: str) -> list[str]:
        return self.image_commands.get(image, ["mock-app"])

    def run(self, **kwargs) -> str:
        cid = f"mock-container-{self.next_id}"
        self.next_id += 1
        self.containers[kwargs["name"]] = {"Id": cid, "Names": kwargs["name"], "State": "running", **kwargs}
        return cid

    def copy_archive(self, name: str, dest_path: str, files: dict[str, str], *, uid: int, gid: int) -> None:
        self.archives.append({
            "name": name,
            "dest_path": dest_path,
            "files": dict(files),
            "uid": uid,
            "gid": gid,
        })

    def stop(self, name: str) -> None:
        if name in self.containers:
            self.containers[name]["State"] = "exited"

    def remove(self, name: str) -> None:
        self.containers.pop(name, None)

    def logs(self, name: str, tail: int | None = None, follow: bool = False) -> str:
        return f"logs for {name}"

    def inspect(self, name: str) -> dict:
        return self.containers.get(name, {})

    def ps(self, name_prefix: str = "toolyard-") -> list[dict]:
        return [c for n, c in self.containers.items() if n.startswith(name_prefix)]
