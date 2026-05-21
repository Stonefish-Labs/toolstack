"""Tests for broker configuration defaults."""

from __future__ import annotations

from broker.config import Config, load_config


def test_config_default_state_dir_uses_xdg_state_home(monkeypatch, tmp_path):
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("BROKER_STATE_DIR", raising=False)
    monkeypatch.delenv("BROKER_APPROVER_SIGNING_SECRET_FILE", raising=False)

    assert Config().state_dir == state_home / "toolstack" / "broker"
    assert load_config().state_dir == state_home / "toolstack" / "broker"


def test_broker_state_dir_env_overrides_xdg_default(monkeypatch, tmp_path):
    override = tmp_path / "broker-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("BROKER_STATE_DIR", str(override))
    monkeypatch.delenv("BROKER_APPROVER_SIGNING_SECRET_FILE", raising=False)

    assert load_config().state_dir == override


def test_broker_toolyard_control_socket_env(monkeypatch, tmp_path):
    socket_path = tmp_path / "toolyard.sock"
    monkeypatch.setenv("BROKER_TOOLYARD_CONTROL_SOCKET", str(socket_path))
    monkeypatch.delenv("BROKER_APPROVER_SIGNING_SECRET_FILE", raising=False)

    assert load_config().toolyard_control_socket == socket_path
