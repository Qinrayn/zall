"""E7 Plugin 版本化支柱: tool_version + 兼容性检查.

Corresponds to:
  MASTER.md §12 Plugin 版本化 (ABSENT -> PARTIAL)
  src/zall/core/tool.py get_tool_version / parse_semver / is_version_compatible

IPR-0: 每个测试含反例.
"""

from __future__ import annotations

from zall.core.tool import (
    ToolRegistry,
    get_tool_version,
    parse_semver,
    is_version_compatible,
)


# ── 最小 fake tool (满足 Tool Protocol) ──


class _FakeTool:
    """Minimal Tool implementation for testing."""

    def __init__(
        self,
        tool_id: str = "fake",
        version: str | None = None,
    ) -> None:
        self._id = tool_id
        self._version = version
        self.schema = {"type": "object", "properties": {}}

    @property
    def tool_id(self) -> str:
        return self._id

    @property
    def tool_version(self) -> str:
        return self._version or "0.0.0"

    def execute(self, args: dict) -> object:
        return None


class _NoVersionTool:
    """Tool without tool_version attribute (legacy)."""

    tool_id = "legacy"
    schema = {}

    def execute(self, args: dict) -> object:
        return None


# ── get_tool_version ──


class TestGetToolVersion:
    def test_returns_version_when_set(self) -> None:
        t = _FakeTool(version="1.2.3")
        assert get_tool_version(t) == "1.2.3"

    def test_defaults_to_zero_when_absent(self) -> None:
        """Counterexample: tool without version -> "0.0.0" (not None/error)."""
        t = _NoVersionTool()
        assert get_tool_version(t) == "0.0.0"


# ── parse_semver ──


class TestParseSemver:
    def test_simple(self) -> None:
        assert parse_semver("1.2.3") == (1, 2, 3)

    def test_two_parts(self) -> None:
        assert parse_semver("2.0") == (2, 0, 0)

    def test_with_prerelease(self) -> None:
        assert parse_semver("1.0.0-rc1") == (1, 0, 0)

    def test_malformed(self) -> None:
        """Counterexample: malformed -> (0,0,0), not raise."""
        assert parse_semver("garbage") == (0, 0, 0)
        assert parse_semver("") == (0, 0, 0)


# ── is_version_compatible ──


class TestIsVersionCompatible:
    def test_exact_match(self) -> None:
        assert is_version_compatible("1.0.0", "1.0.0") is True

    def test_higher_patch(self) -> None:
        assert is_version_compatible("1.0.5", "1.0.0") is True

    def test_higher_minor(self) -> None:
        assert is_version_compatible("1.3.0", "1.0.0") is True

    def test_lower_minor(self) -> None:
        """Counterexample: lower than required -> not compatible."""
        assert is_version_compatible("1.0.0", "1.2.0") is False

    def test_major_mismatch(self) -> None:
        """Counterexample: major difference = breaking, not compatible even if higher."""
        assert is_version_compatible("2.0.0", "1.0.0") is False
        assert is_version_compatible("1.0.0", "2.0.0") is False


# ── ToolRegistry 版本化 ──


class TestToolRegistryVersions:
    def test_tool_versions_property(self) -> None:
        reg = ToolRegistry(tools=(
            _FakeTool("a", "1.0.0"),
            _FakeTool("b", "2.1.3"),
        ))
        assert reg.tool_versions == {"a": "1.0.0", "b": "2.1.3"}

    def test_tool_versions_defaults(self) -> None:
        """Legacy tools without version report "0.0.0"."""
        reg = ToolRegistry(tools=(_NoVersionTool(),))
        assert reg.tool_versions == {"legacy": "0.0.0"}

    def test_check_compatibility_all_pass(self) -> None:
        reg = ToolRegistry(tools=(
            _FakeTool("a", "1.2.0"),
            _FakeTool("b", "2.0.5"),
        ))
        result = reg.check_compatibility({"a": "1.0.0", "b": "2.0.0"})
        assert result == {"a": True, "b": True}

    def test_check_compatibility_mixed(self) -> None:
        reg = ToolRegistry(tools=(
            _FakeTool("a", "1.0.0"),
            _FakeTool("b", "2.0.0"),
        ))
        result = reg.check_compatibility({"a": "1.2.0", "b": "2.0.0"})
        assert result == {"a": False, "b": True}

    def test_check_compatibility_skips_unregistered(self) -> None:
        """Counterexample: unregistered tools not in result."""
        reg = ToolRegistry(tools=(_FakeTool("a", "1.0.0"),))
        result = reg.check_compatibility({"a": "1.0.0", "missing": "1.0.0"})
        assert "a" in result
        assert "missing" not in result

    def test_major_mismatch_incompatible(self) -> None:
        """Counterexample: major mismatch = breaking change."""
        reg = ToolRegistry(tools=(_FakeTool("a", "2.0.0"),))
        result = reg.check_compatibility({"a": "1.0.0"})
        assert result == {"a": False}
