"""web_fetch tool invariant test (§4.2 tool extension).

IPR-0: each test must contain a counterexample.

Counterexample:
  1. 空 URL → success=False
  2. URL 协议不支持 → success=False (仅 http/https)
  3. URL 无法连接 → success=False + 友好错误
  4. 内容类型不支持 → success=False
  5. 超时 → success=False
  6. SSRF 防护: 私有/回环/元数据端点 → success=False
  7. construct后改 success → raise (ToolResult frozen)

工程化: happy-path 用例原依赖外网 example.com (CI 断网即 skip/潜在flaky);
也不可用本地 127.0.0.1 server — 工具自身的 SSRF 防护会正确拒绝回环地址,
测试不应绕过它。故用 httpx.MockTransport + 保留域名 test.invalid
(RFC 2606, 永不可解析) — 工具全链路 (SSRF 检查→scheme→content-type→
流式截断→HTML 解析) 真实执行, 但零外网、零真实连接。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from zall.core.tool import Tool, ToolResult
from zall.tools import web_fetch as web_fetch_mod
from zall.tools.web_fetch import WebFetchTool

_PAGE = (
    b"<html><head><title>Example Local Test Page</title></head>"
    b"<body><h1>Example Content</h1><p>zall local web_fetch test page.</p></body></html>"
)

# RFC 2606 保留的未分配 TLD — DNS 解析必然失败, 但 SSRF 检查按
# "无法解析→放行" 处理, 请求实际由 MockTransport 在进程内完成。
_MOCK_URL = "http://fetch.test.invalid/page"


def _page_handler(request: httpx.Request) -> httpx.Response:
    """Mock 页面: 返回固定 HTML (200, text/html)。"""
    return httpx.Response(
        200,
        content=_PAGE,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


def _make_tool(monkeypatch: pytest.MonkeyPatch, handler: Any) -> WebFetchTool:
    """构造 WebFetchTool, 其 httpx client 换为进程内 MockTransport。"""
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    monkeypatch.setattr(web_fetch_mod, "_get_http_client", lambda: client)
    return WebFetchTool()


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> WebFetchTool:
    """happy-path 工具: 200 HTML 页; 零网络。"""
    return _make_tool(monkeypatch, _page_handler)


@pytest.fixture
def offline_tool(monkeypatch: pytest.MonkeyPatch) -> WebFetchTool:
    """连接失败工具: transport 抛 ConnectError, 模拟不可达主机。"""

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused (mock)")

    return _make_tool(monkeypatch, _refuse)


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
        result = tool.execute({"url": _MOCK_URL})
        assert isinstance(result, ToolResult)


class TestWebFetchHappyPath:
    """正常抓取场景 (MockTransport, 零外网)."""

    def test_fetch_page(self, tool: WebFetchTool) -> None:
        """成功抓取页面."""
        result = tool.execute({"url": _MOCK_URL})
        assert result.success
        assert "example" in result.output.lower()

    def test_artifacts_contain_url(self, tool: WebFetchTool) -> None:
        """artifacts 含 url."""
        result = tool.execute({"url": _MOCK_URL})
        assert result.success
        assert "url" in result.artifacts
        assert result.artifacts["url"] == _MOCK_URL

    def test_artifacts_contain_title(self, tool: WebFetchTool) -> None:
        """artifacts 含 title."""
        result = tool.execute({"url": _MOCK_URL})
        assert result.success
        assert "title" in result.artifacts
        assert "Local" in result.artifacts["title"]

    def test_artifacts_contain_chars(self, tool: WebFetchTool) -> None:
        """artifacts 含 chars."""
        result = tool.execute({"url": _MOCK_URL})
        assert result.success
        assert "chars" in result.artifacts
        assert result.artifacts["chars"] > 0


class TestWebFetchSSRF:
    """SSRF 防护 invariant (回环/私网/元数据必须拒绝, 不随测试环境放宽)."""

    def test_blocks_loopback_ip(self, tool: WebFetchTool) -> None:
        """Counterexample: 127.0.0.1 → success=False (SSRF 防护见效)."""
        result = tool.execute({"url": "http://127.0.0.1:8080/"})
        assert not result.success
        assert "blocked" in result.output.lower()

    def test_blocks_private_ip(self, tool: WebFetchTool) -> None:
        """Counterexample: 192.168.x.x → success=False."""
        result = tool.execute({"url": "http://192.168.1.1/admin"})
        assert not result.success
        assert "blocked" in result.output.lower()

    def test_blocks_localhost_hostname(self, tool: WebFetchTool) -> None:
        """Counterexample: localhost → success=False."""
        result = tool.execute({"url": "http://localhost:8080/"})
        assert not result.success
        assert "blocked" in result.output.lower()

    def test_blocks_metadata_endpoint(self, tool: WebFetchTool) -> None:
        """Counterexample: 169.254.169.254 (云元数据) → success=False."""
        result = tool.execute({"url": "http://169.254.169.254/latest/meta-data/"})
        assert not result.success
        assert "blocked" in result.output.lower()


class TestWebFetchCounterExamples:
    """Counterexample test: verify 输入错误和边界条件 handle."""

    def test_empty_url(self, tool: WebFetchTool) -> None:
        """Counterexample: 空 URL → success=False + 友好 error."""
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

    def test_unreachable_host(self, offline_tool: WebFetchTool) -> None:
        """Counterexample: 无法连接 → 友好的连接 error (零外网)."""
        result = offline_tool.execute({"url": _MOCK_URL})
        assert not result.success
        assert "connection" in result.output.lower()

    def test_result_is_frozen(self, tool: WebFetchTool) -> None:
        """Counterexample: construct 后改 success → must raise (ToolResult frozen)."""
        result = tool.execute({"url": _MOCK_URL})
        with pytest.raises((TypeError, ValueError)):
            result.success = not result.success  # type: ignore[misc]

    def test_output_non_empty_on_failure(self, tool: WebFetchTool) -> None:
        """Counterexample: 即使 fail 也有 output, 不允许静默 fail."""
        result = tool.execute({"url": ""})
        assert not result.success
        assert result.output  # output non-empty

    def test_max_chars_truncation(self, tool: WebFetchTool) -> None:
        """Counterexample: 小 max_chars → output 被 truncate."""
        result = tool.execute({"url": _MOCK_URL, "max_chars": 10})
        assert result.success
        assert len(result.output) <= 10 + 200  # 加截断 prompt 的余量

    def test_nonexistent_domain(self, offline_tool: WebFetchTool) -> None:
        """Counterexample: 不可达域名 → 连接 error (零外网)."""
        result = offline_tool.execute({"url": _MOCK_URL})
        assert not result.success