from __future__ import annotations

import pytest

from conftest import write_tool
from toolyard.registry import get_descriptor, reload_index, walk_tools


def test_walk_tools_and_get_descriptor(tmp_path):
    tools = tmp_path / "tools"
    write_tool(tools / "a", "id: a\ntype: rest\nentrypoint:\n  image: a:latest\n  port: 5001\n")
    write_tool(tools / "b", "id: b\ntype: mcp-http\nentrypoint:\n  image: b:latest\n  port: 5002\n")

    descs = list(walk_tools(tools))
    assert [d.id for d in descs] == ["a", "b"]
    assert get_descriptor(tools, "a").entrypoint.port == 5001
    assert set(reload_index(tools)) == {"a", "b"}


def test_walk_tools_surfaces_invalid_yaml_path(tmp_path):
    tools = tmp_path / "tools"
    write_tool(tools / "bad", "id: Bad\ntype: rest\nentrypoint:\n  image: x\n  port: 1\n")
    with pytest.raises(ValueError, match="bad/toolyard.yaml"):
        list(walk_tools(tools))
