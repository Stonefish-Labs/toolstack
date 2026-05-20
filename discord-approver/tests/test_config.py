"""Tests for approver configuration parsing."""

from __future__ import annotations

import pytest

from discord_approver.config import Settings


def _write(path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _settings_kwargs(tmp_path, **overrides):
    discord_token = tmp_path / "discord.token"
    broker_token = tmp_path / "broker.token"
    _write(discord_token, "discord-token")
    _write(broker_token, "broker-token")
    values = {
        "APPROVER_DISCORD_TOKEN_FILE": discord_token,
        "APPROVER_BROKER_TOKEN_FILE": broker_token,
        "APPROVER_DISCORD_CHANNEL_ID": 123,
        "APPROVER_BROKER_URL": "http://127.0.0.1:8765",
        "APPROVER_ALLOWED_USER_IDS": "111, 222",
        "APPROVER_ALLOWED_ROLE_IDS": "",
    }
    values.update(overrides)
    return values


def test_allowed_user_ids_are_parsed(tmp_path):
    settings = Settings(**_settings_kwargs(tmp_path))

    assert settings.allowed_user_ids == frozenset({111, 222})
    assert settings.allowed_role_ids == frozenset()


def test_allowed_role_ids_are_parsed(tmp_path):
    settings = Settings(
        **_settings_kwargs(
            tmp_path,
            APPROVER_ALLOWED_USER_IDS="",
            APPROVER_ALLOWED_ROLE_IDS="333,444",
        )
    )

    assert settings.allowed_user_ids == frozenset()
    assert settings.allowed_role_ids == frozenset({333, 444})


def test_empty_allowlist_fails_startup(tmp_path):
    with pytest.raises(ValueError, match="at least one Discord allowlist"):
        Settings(
            **_settings_kwargs(
                tmp_path,
                APPROVER_ALLOWED_USER_IDS="",
                APPROVER_ALLOWED_ROLE_IDS="",
            )
        )


def test_invalid_allowlist_id_fails_startup(tmp_path):
    with pytest.raises(ValueError, match="not a numeric Discord ID"):
        Settings(**_settings_kwargs(tmp_path, APPROVER_ALLOWED_USER_IDS="abc"))


def test_broker_signing_secret_file_is_loaded(tmp_path):
    signing_secret = tmp_path / "signing.key"
    _write(signing_secret, "signing-secret")

    settings = Settings(
        **_settings_kwargs(
            tmp_path, APPROVER_BROKER_SIGNING_SECRET_FILE=signing_secret
        )
    )

    assert settings.broker_signing_secret == "signing-secret"
