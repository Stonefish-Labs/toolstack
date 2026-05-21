"""Tests for Toolyard configuration defaults."""

from __future__ import annotations

from toolyard.config import Config, load_config


def test_config_default_state_dir_uses_xdg_state_home(monkeypatch, tmp_path):
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("TOOLYARD_STATE_DIR", raising=False)

    assert Config().state_dir == state_home / "toolstack"
    assert load_config().state_dir == state_home / "toolstack"


def test_toolyard_state_dir_env_overrides_xdg_default(monkeypatch, tmp_path):
    override = tmp_path / "toolyard-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("TOOLYARD_STATE_DIR", str(override))

    assert load_config().state_dir == override


def test_infisical_credentials_dir_defaults_to_xdg_config_home(monkeypatch, tmp_path):
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("TOOLYARD_INFISICAL_CREDENTIALS_DIR", raising=False)

    assert Config().infisical_credentials_dir == config_home / "toolstack" / "infisical"
    assert load_config().infisical_credentials_dir == config_home / "toolstack" / "infisical"


def test_infisical_config_env(monkeypatch, tmp_path):
    credentials_dir = tmp_path / "creds"
    monkeypatch.setenv("TOOLYARD_INFISICAL_HOST", "https://infisical.local")
    monkeypatch.setenv("TOOLYARD_INFISICAL_ENVIRONMENT", "dev")
    monkeypatch.setenv("TOOLYARD_INFISICAL_CREDENTIALS_DIR", str(credentials_dir))
    monkeypatch.setenv("TOOLYARD_INFISICAL_ORGANIZATION_SLUG", "homelab")

    config = load_config()

    assert config.infisical_host == "https://infisical.local"
    assert config.infisical_environment == "dev"
    assert config.infisical_credentials_dir == credentials_dir
    assert config.infisical_organization_slug == "homelab"
