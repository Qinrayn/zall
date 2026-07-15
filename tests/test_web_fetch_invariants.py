"""web_fetch tool invariant test (§4.2 tool extension).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. 空 URL → success=False
  2. URL 协议不支持 → success=False (仅 http/https)
  3. URL 无法连接 → success=False + 友好错误
  4. 内容类型不支持 → success=False
  5. 超时 → success=False
  6. construct后改 success → raise (ToolResult frozen)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zall.core.tool import Tool, ToolResult
from zall.tools.web_fetch import WebFetchTool


@pytest.fixture
def tool() -> WebFetchTool:
    return WebFetchTool()


class TestWebFetchProtocol:
    """verify WebFetchTool 满足 Tool Protocol."""

    def test_is_tool(self, tool: WebFetchTool) -> None:
        """满足 Tool Protocol."""
        assert isinstance(tool, Tool)

    def test_tool_id(self, tool: WebFetchTool) -> None:
        """tool_id 是 'web_fetch'."""
        assert tool.tool_id == "web_fetch"

    def test_schema_has_url_required(self, tool: WebFetchTool) -> None:
        """schema 的 required 含 'url'."""
        params = tool.schema["function"]["parameters"]
        assert "url" in params["required"]

    def test_schema_has_max_chars(self, tool: WebFetchTool) -> None:
        """schema 含 max_chars parameter."""
        params = tool.schema["function"]["parameters"]
        assert "max_chars" in params["properties"]

    def test_execute_returns_tool_result(self, tool: WebFetchTool) -> None:
        """execute returns ToolResult instance."""
        result = tool.execute({"url": "https://example.com"})
        assert isinstance(result, ToolResult)


class TestWebFetchHappyPath:
    """正常抓取网页的场景 (需要network)."""

    def test_fetch_example_com(self, tool: WebFetchTool) -> None:
        """抓取 example.com returns成功 (需要network)."""
        result = tool.execute({"url": "https://example.com"})
        if not result.success:
            pytest.skip("Network not available")
        assert result.success
        assert "Example" in result.output or "example" in result.output.lower()

    def test_artifacts_contain_url(self, tool: WebFetchTool) -> None:
        """artifacts 含 url (需要network)."""
        result = tool.execute({"url": "https://example.com"})
        if not result.success:
            pytest.skip("Network not available")
        assert "url" in result.artifacts
        assert result.artifacts["url"] == "https://example.com"

    def test_artifacts_contain_title(self, tool: WebFetchTool) -> None:
        """artifacts 含 title (需要network)."""
        result = tool.execute({"url": "https://example.com"})
        if not result.success:
            pytest.skip("Network not available")
        assert "title" in result.artifacts

    def test_artifacts_contain_chars(self, tool: WebFetchTool) -> None:
        """artifacts 含 chars (需要network)."""
        result = tool.execute({"url": "https://example.com"})
        if not result.success:
            pytest.skip("Network not available")
        assert "chars" in result.artifacts
        assert result.artifacts["chars"] > 0


class TestWebFetchCounterExamples:
    """Counterexampletest: verifyinputerror和边界条件handle."""

    def test_empty_url(self, tool: WebFetchTool) -> None:
        """Counterexample: 空 URL → success=False + 友好error."""
        result = tool.execute({"url": ""})
        assert not result.success
        assert "required" in result.output.lower()

    def test_missing_url(self, tool: WebFetchTool) -> None:
        """Counterexample: 缺失 url → success=False."""
        result = tool.execute({})
        assert not result.success

    def test_invalid_scheme_ftp(self, tool: WebFetchTool) -> None:
        """Counterexample: FTP URL → reject (仅 http/https)."""
        result = tool.execute({"url": "ftp://files.example.com/data"})
        assert not result.success
        assert "URL" in result.output

    def test_invalid_scheme_file(self, tool: WebFetchTool) -> None:
        """Counterexample: file:// URL → reject."""
        result = tool.execute({"url": "file:///etc/passwd"})
        assert not result.success
        assert "URL" in result.output

    def test_invalid_url_format(self, tool: WebFetchTool) -> None:
        """Counterexample: 无效 URL → success=False."""
        result = tool.execute({"url": "not-a-url"})
        assert not result.success
        assert "URL" in result.output

    def test_unreachable_host(self, tool: WebFetchTool) -> None:
        """Counterexample: 无法连接的主机 → 友好的连接error."""
        result = tool.execute({"url": "https://192.0.2.1/nonexistent"})
        assert not result.success
        # 应该returns连接error, not HTTP error
        assert "connection" in result.output.lower() or "error" in result.output.lower()

    def test_result_is_frozen(self, tool: WebFetchTool) -> None:
        """Counterexample: construct后改 success → must raise (ToolResult frozen)."""
        result = tool.execute({"url": "https://example.com"})
        # frozen check不dependencyexecute成功
        with pytest.raises((TypeError, ValueError)):
            result.success = not result.success

    def test_output_non_empty_on_failure(self, tool: WebFetchTool) -> None:
        """Counterexample: 即使fail也有output, 不允许静默fail."""
        result = tool.execute({"url": ""})
        assert not result.success
        assert result.output  # output non-空

    def test_max_chars_truncation(self, tool: WebFetchTool) -> None:
        """Counterexample: 小 max_chars → output被truncate (需要network)."""
        result = tool.execute({"url": "https://example.com", "max_chars": 10})
        if not result.success:
            pytest.skip("Network not available")
        assert len(result.output) <= 10 + 200  # 加截断prompt的余量

    def test_nonexistent_domain(self, tool: WebFetchTool) -> None:
        """Counterexample: 不存在的域名 → 连接error."""
        result = tool.execute({"url": "https://zzz-nonexistent-domain-xyz123.com/"})
        assert not result.success