"""Secret resolution for toolyard-managed containers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from toolyard.models import ToolDescriptor


class SecretResolver(Protocol):
    def resolve(self, vault: str, item: str, field: str) -> str: ...


class SecretWriter(Protocol):
    def update(self, vault: str, item: str, field: str, value: str) -> None: ...


class ConnectSecretResolver:
    def __init__(self, host: str, token_file: str | Path):
        shim_dir = Path(__file__).resolve().parents[3] / "lib" / "op-connect-shim"
        if str(shim_dir) not in sys.path:
            sys.path.insert(0, str(shim_dir))
        from op_connect_shim import OnePasswordConnect

        self._op = OnePasswordConnect(host=host, token_file=token_file)

    def resolve(self, vault: str, item: str, field: str) -> str:
        return self._op.get_field(vault=vault, item=item, field=field)


class ConnectSecretWriter:
    def __init__(self, host: str, token_file: str | Path):
        shim_dir = Path(__file__).resolve().parents[3] / "lib" / "op-connect-shim"
        if str(shim_dir) not in sys.path:
            sys.path.insert(0, str(shim_dir))
        from op_connect_shim import OnePasswordConnect

        self._op = OnePasswordConnect(host=host, token_file=token_file)

    def update(self, vault: str, item: str, field: str, value: str) -> None:
        self._op.update_field(vault=vault, item=item, field=field, value=value)


class MockSecretResolver:
    def __init__(self, values: dict[tuple[str, str, str], str]):
        self.values = values

    def resolve(self, vault: str, item: str, field: str) -> str:
        return self.values[(vault, item, field)]


class MockSecretWriter:
    def __init__(self):
        self.updates: list[tuple[str, str, str, str]] = []

    def update(self, vault: str, item: str, field: str, value: str) -> None:
        self.updates.append((vault, item, field, value))


def resolve_secrets(*, descriptor: ToolDescriptor, resolver: SecretResolver) -> dict[str, str]:
    """Resolve all descriptor secrets into an in-memory name/value mapping."""

    values: dict[str, str] = {}
    for ref in descriptor.secrets:
        item = ref.item or descriptor.id
        values[ref.name] = resolver.resolve(ref.vault, item, ref.field)
    return values
