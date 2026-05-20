#!/usr/bin/env python3
"""Tiny dependency-free 1Password Connect helper.

Designed for small Python services and MCP servers that need to read, and
optionally write, secrets through a local 1Password Connect REST API.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_CONFIG_FILE = "~/.config/op-connect-shim.env"


class OnePasswordConnectError(RuntimeError):
    """Base exception for Connect shim failures."""


class OnePasswordLookupError(OnePasswordConnectError):
    """Raised when a vault, item, or field cannot be found uniquely."""


def _parse_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _read_token_file(path: str | pathlib.Path | None) -> str | None:
    if not path:
        return None
    token_path = pathlib.Path(path).expanduser()
    if not token_path.is_file():
        return None

    content = token_path.read_text(encoding="utf-8").strip()
    if "=" not in content:
        return content or None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() in {"OP_CONNECT_TOKEN", "CONNECT_TOKEN"}:
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value or None
    return None


def _quote_filter(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _require_nonempty(value: str | None, name: str) -> str:
    if not value:
        raise OnePasswordConnectError(f"missing required {name}")
    return value


@dataclass(frozen=True)
class FieldRef:
    vault: str
    item: str
    field: str


class OnePasswordConnect:
    """Small 1Password Connect REST client.

    Resolution order for host and token:
    1. explicit constructor args
    2. environment variables
    3. env-style config file

    Supported config keys:
    - OP_CONNECT_HOST
    - OP_CONNECT_TOKEN
    - CONNECT_TOKEN
    - OP_CONNECT_TOKEN_FILE
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        token: str | None = None,
        token_file: str | pathlib.Path | None = None,
        config_file: str | pathlib.Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        config_path = pathlib.Path(
            config_file
            or os.environ.get("OP_CONNECT_CONFIG")
            or DEFAULT_CONFIG_FILE
        ).expanduser()
        config = _parse_env_file(config_path)

        resolved_host = (
            host
            or os.environ.get("OP_CONNECT_HOST")
            or config.get("OP_CONNECT_HOST")
        )
        resolved_token = (
            token
            or _read_token_file(token_file)
            or os.environ.get("OP_CONNECT_TOKEN")
            or os.environ.get("CONNECT_TOKEN")
            or _read_token_file(os.environ.get("OP_CONNECT_TOKEN_FILE"))
            or config.get("OP_CONNECT_TOKEN")
            or config.get("CONNECT_TOKEN")
            or _read_token_file(config.get("OP_CONNECT_TOKEN_FILE"))
        )

        self.host = _require_nonempty(resolved_host, "OP_CONNECT_HOST").rstrip("/")
        self.token = _require_nonempty(resolved_token, "OP_CONNECT_TOKEN")
        self.timeout = timeout
        self._vault_cache: dict[str, str] = {}
        self._item_cache: dict[tuple[str, str], str] = {}

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        url = f"{self.host}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "op-connect-shim/1.0",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            message = exc.reason
            try:
                payload = exc.read().decode("utf-8", errors="replace")
                if payload:
                    parsed = json.loads(payload)
                    message = parsed.get("message") or parsed.get("error") or message
            except Exception:
                pass
            raise OnePasswordConnectError(
                f"Connect API {method} {path} failed with HTTP {exc.code}: {message}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OnePasswordConnectError(
                f"could not reach Connect API at {self.host}: {exc.reason}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise OnePasswordConnectError(
                f"Connect API {method} {path} timed out after {self.timeout}s"
            ) from exc

        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise OnePasswordConnectError(
                f"Connect API {method} {path} returned invalid JSON"
            ) from exc

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def list_vaults(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v1/vaults")

    def get_vault_id(self, vault: str) -> str:
        if vault in self._vault_cache:
            return self._vault_cache[vault]

        records = self.request(
            "GET",
            "/v1/vaults",
            query={"filter": f"name eq {_quote_filter(vault)}"},
        )
        match = self._unique(records, key="name", value=vault, kind="vault")
        vault_id = self._require_record_id(match, "vault", vault)
        self._vault_cache[vault] = vault_id
        return vault_id

    def get_item_id(self, vault: str, item: str) -> str:
        cache_key = (vault, item)
        if cache_key in self._item_cache:
            return self._item_cache[cache_key]

        vault_id = self.get_vault_id(vault)
        records = self.request(
            "GET",
            f"/v1/vaults/{urllib.parse.quote(vault_id)}/items",
            query={"filter": f"title eq {_quote_filter(item)}"},
        )
        match = self._unique(records, key="title", value=item, kind="item")
        item_id = self._require_record_id(match, "item", item)
        self._item_cache[cache_key] = item_id
        return item_id

    def get_item(self, vault: str, item: str) -> dict[str, Any]:
        vault_id = self.get_vault_id(vault)
        item_id = self.get_item_id(vault, item)
        record = self.request(
            "GET",
            f"/v1/vaults/{urllib.parse.quote(vault_id)}/items/{urllib.parse.quote(item_id)}",
        )
        if not isinstance(record, dict):
            raise OnePasswordConnectError("Connect API returned unexpected item shape")
        return record

    def get_field(self, vault: str, item: str, field: str) -> str:
        record = self.get_item(vault, item)
        return self.get_field_from_item(record, field)

    def get_fields(self, vault: str, item: str, fields: list[str]) -> dict[str, str]:
        record = self.get_item(vault, item)
        return {field: self.get_field_from_item(record, field) for field in fields}

    def get_field_from_item(self, item_record: dict[str, Any], field: str) -> str:
        matches: list[str] = []
        for candidate in item_record.get("fields") or []:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("label") == field:
                value = candidate.get("value")
                if isinstance(value, str) and value:
                    matches.append(value)

        if not matches:
            raise OnePasswordLookupError(f"field not found or empty: {field!r}")
        if len(matches) > 1:
            raise OnePasswordLookupError(f"field label is ambiguous: {field!r}")
        return matches[0]

    def update_field(self, vault: str, item: str, field: str, value: str) -> dict[str, Any]:
        """Replace a field value by label.

        Requires a token with write permission. The token also needs read access
        because Connect patch paths use item and field IDs.
        """

        vault_id = self.get_vault_id(vault)
        item_id = self.get_item_id(vault, item)
        record = self.get_item(vault, item)
        field_id = self._field_id_by_label(record, field)
        patch = [{"op": "replace", "path": f"/fields/{field_id}/value", "value": value}]
        updated = self.request(
            "PATCH",
            f"/v1/vaults/{urllib.parse.quote(vault_id)}/items/{urllib.parse.quote(item_id)}",
            body=patch,
        )
        if not isinstance(updated, dict):
            raise OnePasswordConnectError("Connect API returned unexpected update shape")
        return updated

    def create_password_item(
        self,
        *,
        vault: str,
        title: str,
        fields: dict[str, str],
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a simple Password item with concealed fields.

        Requires a token with write permission.
        """

        vault_id = self.get_vault_id(vault)
        body = {
            "vault": {"id": vault_id},
            "title": title,
            "category": "PASSWORD",
            "tags": tags or [],
            "fields": [
                {"label": label, "type": "CONCEALED", "value": secret}
                for label, secret in fields.items()
            ],
        }
        created = self.request(
            "POST",
            f"/v1/vaults/{urllib.parse.quote(vault_id)}/items",
            body=body,
        )
        if not isinstance(created, dict):
            raise OnePasswordConnectError("Connect API returned unexpected create shape")
        return created

    @staticmethod
    def _unique(records: Any, *, key: str, value: str, kind: str) -> dict[str, Any]:
        if not isinstance(records, list):
            raise OnePasswordConnectError(f"Connect API returned unexpected {kind} list")
        matches = [record for record in records if isinstance(record, dict) and record.get(key) == value]
        if not matches:
            raise OnePasswordLookupError(f"{kind} not found: {value!r}")
        if len(matches) > 1:
            raise OnePasswordLookupError(f"{kind} lookup is ambiguous: {value!r}")
        return matches[0]

    @staticmethod
    def _require_record_id(record: dict[str, Any], kind: str, name: str) -> str:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise OnePasswordConnectError(f"{kind} {name!r} did not include an id")
        return record_id

    @staticmethod
    def _field_id_by_label(item_record: dict[str, Any], label: str) -> str:
        field_ids: list[str] = []
        for field in item_record.get("fields") or []:
            if not isinstance(field, dict):
                continue
            if field.get("label") == label and isinstance(field.get("id"), str):
                field_ids.append(field["id"])
        if not field_ids:
            raise OnePasswordLookupError(f"field not found: {label!r}")
        if len(field_ids) > 1:
            raise OnePasswordLookupError(f"field label is ambiguous: {label!r}")
        return field_ids[0]


def _main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] != "get":
        print("usage: op_connect_shim.py get <vault> <item> <field>", file=sys.stderr)
        return 2
    client = OnePasswordConnect()
    print(client.get_field(argv[2], argv[3], argv[4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
