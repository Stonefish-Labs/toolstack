"""Tests for Discord approval allowlist helpers."""

from __future__ import annotations

from types import SimpleNamespace

from discord_approver.bot import _approver_label, _is_allowed_user


def _user(user_id: int, *, role_ids: tuple[int, ...] = ()):
    roles = [SimpleNamespace(id=role_id) for role_id in role_ids]
    return SimpleNamespace(id=user_id, name="alice", roles=roles)


def test_user_id_allowlist_permits_user():
    assert _is_allowed_user(_user(111), frozenset({111}), frozenset())


def test_role_id_allowlist_permits_member_role():
    assert _is_allowed_user(_user(111, role_ids=(222,)), frozenset(), frozenset({222}))


def test_user_without_allowed_id_or_role_is_denied():
    assert not _is_allowed_user(_user(111, role_ids=(222,)), frozenset({333}), frozenset({444}))


def test_approver_label_includes_stable_discord_id():
    assert _approver_label(_user(111)) == "alice (111)"
