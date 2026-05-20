"""Tests for registry.py — tool descriptor loading + atomic reload."""

from __future__ import annotations

from pathlib import Path

from broker import registry


def _write_tool(tools_dir: Path, tool_id: str, content: str) -> Path:
    tool_dir = tools_dir / tool_id
    tool_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = tool_dir / "toolyard.yaml"
    yaml_path.write_text(content)
    return yaml_path


# ── Basic loading ──────────────────────────────────────────────────


def test_load_empty_dir(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    result = registry.load_registry(tools_dir)
    assert result == {}


def test_load_nonexistent_dir(tmp_path):
    result = registry.load_registry(tmp_path / "nope")
    assert result == {}


def test_load_tool_descriptor(tmp_path):
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "media", """
id: media
type: rest
entrypoint:
  build: .
  port: 4502
operations:
  - { op: get_playback_state, risk: read }
  - { op: skip_track, risk: write }
""")
    result = registry.load_registry(tools_dir)
    assert "media" in result
    desc = result["media"]
    assert desc.id == "media"
    assert desc.type == "rest"
    assert desc.port == 4502
    assert desc.enabled is True
    assert len(desc.operations) == 2


def test_get_tool_after_load(tmp_path):
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "tasks", """
id: tasks
type: rest
entrypoint:
  build: .
  port: 4503
""")
    registry.load_registry(tools_dir)
    desc = registry.get_tool("tasks")
    assert desc is not None
    assert desc.port == 4503
    assert registry.get_tool("nonexistent") is None


# ── enabled flag ────────────────────────────────────────────────


def test_disabled_tool_not_registered(tmp_path):
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "ghost", """
id: ghost
type: rest
enabled: false
entrypoint:
  build: .
  port: 4599
""")
    _write_tool(tools_dir, "live", """
id: live
type: rest
entrypoint:
  build: .
  port: 4600
""")
    result = registry.load_registry(tools_dir)
    assert "live" in result
    assert "ghost" not in result
    assert registry.get_tool("ghost") is None


# ── Validation: invalid descriptors are skipped, valid ones survive ─


def test_invalid_type_skipped(tmp_path):
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "bad", """
id: bad
type: graphql
entrypoint:
  port: 1234
""")
    _write_tool(tools_dir, "good", """
id: good
type: rest
entrypoint:
  port: 5000
""")
    result = registry.load_registry(tools_dir)
    assert "good" in result
    assert "bad" not in result


def test_invalid_port_skipped(tmp_path):
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "noport", """
id: noport
type: rest
entrypoint:
  build: .
""")
    _write_tool(tools_dir, "badport", """
id: badport
type: rest
entrypoint:
  port: 99999
""")
    _write_tool(tools_dir, "good", """
id: good
type: rest
entrypoint:
  port: 5000
""")
    result = registry.load_registry(tools_dir)
    assert set(result.keys()) == {"good"}


def test_corrupt_yaml_skipped(tmp_path):
    """One corrupt toolyard.yaml does not prevent valid ones from loading."""
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "corrupt", "this is not: : : valid yaml [")
    _write_tool(tools_dir, "good", """
id: good
type: rest
entrypoint:
  port: 5000
""")
    result = registry.load_registry(tools_dir)
    assert "good" in result
    assert "corrupt" not in result


def test_missing_toolyard_yaml_ignored(tmp_path):
    """Directories without a toolyard.yaml are silently ignored."""
    tools_dir = tmp_path / "tools"
    (tools_dir / "no-yaml-here").mkdir(parents=True)
    _write_tool(tools_dir, "good", """
id: good
type: rest
entrypoint:
  port: 5000
""")
    result = registry.load_registry(tools_dir)
    assert "good" in result
    assert len(result) == 1


# ── Atomic reload ──────────────────────────────────────────────────


def test_reload_picks_up_new_tools(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    registry.load_registry(tools_dir)
    assert len(registry.list_tools()) == 0

    _write_tool(tools_dir, "added", """
id: added
type: rest
entrypoint:
  port: 5000
""")
    registry.reload()
    assert "added" in registry.list_tools()


def test_reload_removes_deleted_tools(tmp_path):
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "ephemeral", """
id: ephemeral
type: rest
entrypoint:
  port: 5000
""")
    registry.load_registry(tools_dir)
    assert "ephemeral" in registry.list_tools()

    # Delete the tool's yaml
    (tools_dir / "ephemeral" / "toolyard.yaml").unlink()
    registry.reload()
    assert "ephemeral" not in registry.list_tools()


def test_reload_atomic_partial_failure(tmp_path):
    """A bad new file does not corrupt the existing registry's good entries.

    After reload, the new map contains only valid entries — the rest are
    skipped with a log warning. The swap is all-or-nothing.
    """
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "alpha", """
id: alpha
type: rest
entrypoint:
  port: 5000
""")
    _write_tool(tools_dir, "beta", """
id: beta
type: rest
entrypoint:
  port: 5001
""")
    registry.load_registry(tools_dir)
    assert set(registry.list_tools().keys()) == {"alpha", "beta"}

    # Corrupt one file
    (tools_dir / "alpha" / "toolyard.yaml").write_text("invalid: : : :")
    # Add a new valid one
    _write_tool(tools_dir, "gamma", """
id: gamma
type: rest
entrypoint:
  port: 5002
""")
    registry.reload()
    after = registry.list_tools()
    # alpha is now corrupt → dropped. beta and gamma survive.
    assert "alpha" not in after
    assert "beta" in after
    assert "gamma" in after


def test_duplicate_id_keeps_first(tmp_path):
    """Two directories declaring the same id: the second is rejected."""
    tools_dir = tmp_path / "tools"
    # Directory name is "a" but declares id "shared"
    _write_tool(tools_dir, "a", """
id: shared
type: rest
entrypoint:
  port: 5000
""")
    # Directory name is "b" but ALSO declares id "shared"
    _write_tool(tools_dir, "b", """
id: shared
type: rest
entrypoint:
  port: 5001
""")
    result = registry.load_registry(tools_dir)
    assert "shared" in result
    # First sorted directory wins (a comes before b alphabetically)
    assert result["shared"].port == 5000


def test_id_defaults_to_directory_name(tmp_path):
    """If id is omitted, use the directory name."""
    tools_dir = tmp_path / "tools"
    _write_tool(tools_dir, "mytool", """
type: rest
entrypoint:
  port: 5000
""")
    result = registry.load_registry(tools_dir)
    assert "mytool" in result
    assert result["mytool"].id == "mytool"
