"""Secret resolution for toolyard-managed containers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from toolyard.models import ToolDescriptor


class SecretResolver(Protocol):
    def resolve(self, vault: str, item: str, field: str) -> str: ...


class SecretWriter(Protocol):
    def update(self, vault: str, item: str, field: str, value: str) -> None: ...


@dataclass(frozen=True)
class InfisicalCredentials:
    client_id: str
    client_secret: str
    source: Path


def load_infisical_credentials(path: str | Path) -> InfisicalCredentials:
    """Load a local machine-identity credential file.

    Files are intentionally simple env-style assignments so they can be
    provisioned without introducing a second config format.
    """

    source = Path(path)
    values: dict[str, str] = {}
    for line_no, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            raise ValueError(f"{source}:{line_no}: expected KEY=value")
        key = key.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    client_id = values.get("infisical_client_id") or values.get("client_id")
    client_secret = values.get("infisical_client_secret") or values.get("client_secret")
    missing = []
    if not client_id:
        missing.append("INFISICAL_CLIENT_ID")
    if not client_secret:
        missing.append("INFISICAL_CLIENT_SECRET")
    if missing:
        raise ValueError(f"{source}: missing {', '.join(missing)}")
    return InfisicalCredentials(client_id=client_id, client_secret=client_secret, source=source)


class InfisicalSecretResolver:
    def __init__(
        self,
        *,
        host: str,
        credentials_dir: str | Path,
        environment: str,
        organization_slug: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.host = host.rstrip("/")
        self.credentials_dir = Path(credentials_dir)
        self.environment = environment
        self.organization_slug = organization_slug
        self._client = client or httpx.Client(timeout=15)
        self._tokens: dict[Path, tuple[str, float]] = {}
        self._project_ids: dict[tuple[Path, str], str] = {}
        self._secret_cache: dict[tuple[Path, str, str, str], list[dict]] = {}

    def resolve(self, vault: str, item: str, field: str) -> str:
        creds = self._credentials_for_item(item)
        project_id = self._project_id(creds, vault)
        secret_path = _secret_path(item)
        for secret in self._list_secrets(creds, project_id, secret_path):
            if secret.get("secretKey") == field:
                value = secret.get("secretValue")
                if not isinstance(value, str):
                    raise ValueError(
                        f"Infisical secret {vault}{secret_path}/{field} has no string value"
                    )
                return value
        raise KeyError(f"Infisical secret {vault}{secret_path}/{field} not found")

    def update(self, vault: str, item: str, field: str, value: str) -> None:
        creds = self._credentials_for_item(item)
        project_id = self._project_id(creds, vault)
        secret_path = _secret_path(item)
        response = self._client.patch(
            f"{self.host}/api/v4/secrets/{quote(field, safe='')}",
            headers=self._auth_headers(creds),
            json={
                "projectId": project_id,
                "environment": self.environment,
                "secretValue": value,
                "secretPath": secret_path,
                "type": "shared",
            },
        )
        self._raise_for_status(response)
        self._secret_cache.pop((creds.source, project_id, self.environment, secret_path), None)

    def _credentials_for_item(self, item: str) -> InfisicalCredentials:
        path = self.credentials_dir / f"{_credential_stem(item)}.env"
        if not path.exists():
            raise FileNotFoundError(
                f"missing Infisical credentials for {item!r}: expected {path}"
            )
        return load_infisical_credentials(path)

    def _access_token(self, creds: InfisicalCredentials) -> str:
        cached = self._tokens.get(creds.source)
        now = time.time()
        if cached is not None:
            token, expires_at = cached
            if now < expires_at - 30:
                return token

        body = {"clientId": creds.client_id, "clientSecret": creds.client_secret}
        if self.organization_slug:
            body["organizationSlug"] = self.organization_slug
        response = self._client.post(
            f"{self.host}/api/v1/auth/universal-auth/login",
            headers={"Content-Type": "application/json"},
            json=body,
        )
        self._raise_for_status(response)
        payload = response.json()
        token = payload.get("accessToken")
        if not isinstance(token, str) or not token:
            raise ValueError("Infisical login response did not include accessToken")
        expires_in = payload.get("expiresIn")
        ttl = float(expires_in) if isinstance(expires_in, int | float) else 600.0
        self._tokens[creds.source] = (token, now + ttl)
        return token

    def _auth_headers(self, creds: InfisicalCredentials) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token(creds)}"}

    def _project_id(self, creds: InfisicalCredentials, vault: str) -> str:
        key = (creds.source, vault)
        if key in self._project_ids:
            return self._project_ids[key]

        response = self._client.get(
            f"{self.host}/api/v1/projects",
            headers=self._auth_headers(creds),
        )
        self._raise_for_status(response)
        payload = response.json()
        projects = payload.get("projects") if isinstance(payload, dict) else None
        if not isinstance(projects, list):
            raise ValueError("Infisical projects response did not include projects")
        for project in projects:
            if not isinstance(project, dict):
                continue
            matches = {
                value
                for value in (
                    project.get("id"),
                    project.get("_id"),
                    project.get("name"),
                    project.get("slug"),
                )
                if isinstance(value, str)
            }
            if vault in matches:
                project_id = project.get("id") or project.get("_id")
                if not isinstance(project_id, str) or not project_id:
                    raise ValueError(f"Infisical project {vault!r} has no id")
                self._project_ids[key] = project_id
                return project_id
        raise KeyError(f"Infisical project {vault!r} not found")

    def _list_secrets(
        self, creds: InfisicalCredentials, project_id: str, secret_path: str
    ) -> list[dict]:
        key = (creds.source, project_id, self.environment, secret_path)
        if key in self._secret_cache:
            return self._secret_cache[key]
        response = self._client.get(
            f"{self.host}/api/v4/secrets",
            headers=self._auth_headers(creds),
            params={
                "projectId": project_id,
                "environment": self.environment,
                "secretPath": secret_path,
                "viewSecretValue": "true",
                "expandSecretReferences": "true",
                "includeImports": "true",
            },
        )
        self._raise_for_status(response)
        payload = response.json()
        secrets: list[dict] = []
        if isinstance(payload, dict):
            direct = payload.get("secrets")
            if isinstance(direct, list):
                secrets.extend(secret for secret in direct if isinstance(secret, dict))
            imports = payload.get("imports")
            if isinstance(imports, list):
                for imported in imports:
                    imported_secrets = (
                        imported.get("secrets") if isinstance(imported, dict) else None
                    )
                    if isinstance(imported_secrets, list):
                        secrets.extend(
                            secret for secret in imported_secrets if isinstance(secret, dict)
                        )
        self._secret_cache[key] = secrets
        return secrets

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            request = response.request
            raise RuntimeError(
                f"Infisical API request failed: "
                f"{request.method} {request.url.path}: HTTP {response.status_code}"
            ) from exc


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


def _secret_path(item: str) -> str:
    stripped = item.strip("/")
    return f"/{stripped}" if stripped else "/"


def _credential_stem(item: str) -> str:
    stripped = item.strip("/") or "root"
    return stripped.replace("/", "__")
