"""Tests for cli.py — exercise subcommands against temp state."""

from __future__ import annotations

import os
import json
from pathlib import Path
from unittest import mock

import pytest

from broker.cli import main


@pytest.fixture
def cli_env(tmp_path, sample_profiles_dir):
    """Set up env vars for CLI commands pointing at temp dirs."""
    env = {
        "BROKER_STATE_DIR": str(tmp_path / "state"),
        "BROKER_TOOLS_DIR": str(tmp_path / "tools"),
        "BROKER_POLICIES_DIR": str(sample_profiles_dir),
        "BROKER_ALLOW_UNKNOWN_TOOLS": "true",
    }
    (tmp_path / "tools").mkdir(exist_ok=True)
    with mock.patch.dict(os.environ, env):
        yield tmp_path


def test_init_db(cli_env, capsys):
    main(["init-db"])
    output = capsys.readouterr().out
    assert "initialized" in output
    assert (cli_env / "state" / "broker.sqlite3").exists()


def test_create_caller(cli_env, capsys):
    main(["init-db"])
    main(["create-caller", "--name", "agent.test", "--profile", "home-default"])
    output = capsys.readouterr().out
    assert "agent.test" in output
    assert "BEARER TOKEN" in output


def test_list_callers(cli_env, capsys):
    main(["init-db"])
    main(["create-caller", "--name", "agent.lc", "--profile", "home-default"])
    capsys.readouterr()  # clear

    main(["list-callers"])
    output = capsys.readouterr().out
    assert "agent.lc" in output


def test_list_callers_json(cli_env, capsys):
    main(["init-db"])
    main(["create-caller", "--name", "agent.json", "--profile", "p"])
    capsys.readouterr()

    main(["list-callers", "--json"])
    output = capsys.readouterr().out
    data = json.loads(output)
    assert isinstance(data, list)
    assert data[0]["name"] == "agent.json"


def test_revoke_caller(cli_env, capsys):
    main(["init-db"])
    main(["create-caller", "--name", "agent.rev", "--profile", "p"])
    capsys.readouterr()

    main(["revoke-caller", "agent.rev"])
    output = capsys.readouterr().out
    assert "revoked" in output


def test_list_tokens(cli_env, capsys):
    main(["init-db"])
    main(["create-caller", "--name", "agent.lt", "--profile", "p"])
    capsys.readouterr()

    main(["list-tokens"])
    output = capsys.readouterr().out
    assert "agent.lt" in output


def test_list_requests_empty(cli_env, capsys):
    main(["init-db"])
    main(["list-requests"])
    output = capsys.readouterr().out
    assert "no requests" in output


def test_audit_empty(cli_env, capsys):
    main(["init-db"])
    main(["audit"])
    # After init-db there might be no events, or just the create events
    capsys.readouterr()  # Just verify it doesn't crash


def test_reload_registry(cli_env, capsys):
    main(["init-db"])
    main(["reload-registry"])
    output = capsys.readouterr().out
    assert "reloaded" in output
