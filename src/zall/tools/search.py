"""zall.tools.search — Web search tool (DuckDuckGo, zero-cost).

Corresponds to:
  Phase 4: Web search capability — agent can search the web for information
  Design: model-agnostic, no API key needed, privacy-respecting

ACI Design notes:
  - Uses DuckDuckGo Lite (no API key, free, privacy-friendly)
  - Returns structured results: title, snippet, URL
  - Auto-truncates results to prevent context pollution
  - Timeout-safe (does not block agent loop)

IPR constraints:
  IPR-0: invariant tests at tests/test_search_tool.py (to be created)
  IPR-3: only stdlib + httpx, no model SDK
"""

from __future__ import annotations

import atexit
import re
from typing import Any
from urllib.parse import unquote

import httpx

from zall.core.tool import ToolResult

# default最大结果数
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
# searchtimeout — 从中国等地区访问搜索引擎可能较慢
SEARCH_TIMEOUT = 30.0
# 备用超时
FALLBACK_TIMEOUT = 20.0
# snippet truncate长度
SNIPPET_MAX = 300
# 最大重试次数
MAX_RETRIES = 2

# O5: shared httpx.Client for connection pooling across search calls
_SEARCH_HTTP_CLIENT: httpx.Client | None = None


def _get_search_http_client() -> httpx.Client:
    """Lazily create and return the shared httpx.Client."""
    global _SEARCH_HTTP_CLIENT
    if _SEARCH_HTTP_CLIENT is None:
        import httpx
        _SEARCH_HTTP_CLIENT = httpx.Client(
            timeout=SEARCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; zall/1.0; +https://github.com/zall)"
                ),
            },
        )
    return _SEARCH_HTTP_CLIENT


def close_search_http_client() -> None:
    """Close the shared httpx.Client (call at shutdown)."""
    global _SEARCH_HTTP_CLIENT
    if _SEARCH_HTTP_CLIENT is not None:
        try:
            _SEARCH_HTTP_CLIENT.close()
        except Exception:
            pass
        _SEARCH_HTTP_CLIENT = None

# v0.4.8: 注册 atexit 处理器, 确保进程退出时关闭共享 HTTP 连接池
atexit.register(close_search_http_client)


class SearchTool:
    """Web search tool — 通过 DuckDuckGo search互联网。

    ACI design decisions:
      - 零成本: 使用 DuckDuckGo Lite HTML 版, 无需 API key
      - 结构化返回: 标题 + 摘要 + URL, 模型易消费
      - 结果数量限制: 防止上下文污染
      - 仅文本搜索: 不支持图片/视频搜索 (保持轻量)

    schema 设计:
      query:       必填, 搜索关键词
      max_results: 可选, 最大返回结果数 (默认 5, 最大 10)
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "web_search"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web for information. "
                    "Returns a list of search results with title, snippet, and URL. "
                    "Useful for finding current information, documentation, tutorials, "
                    "news, and answers to questions. "
                    "Uses DuckDuckGo search engine (no API key required). "
                    f"Max {MAX_RESULTS_LIMIT} results per query."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (keywords or natural language question)",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum results to return (default: {DEFAULT_MAX_RESULTS}, max: {MAX_RESULTS_LIMIT})",
                            "default": DEFAULT_MAX_RESULTS,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(
                success=False,
                output="[ERROR: query argument is required for web_search]",
                error="query is required",
            )

        max_results = args.get("max_results", DEFAULT_MAX_RESULTS)
        if not isinstance(max_results, int) or max_results < 1:
            max_results = DEFAULT_MAX_RESULTS
        max_results = min(max_results, MAX_RESULTS_LIMIT)

        return self._search(query, max_results)

    def _search(self, query: str, max_results: int) -> ToolResult:
        """通过 DuckDuckGo 搜索, 带重试和备用搜索引擎。

        实现: 
          1. 首选 DuckDuckGo Lite (免费, 无需 key)
          2. 超时/失败后重试 1 次
          3. 仍失败则降级到 Bing HTML 搜索 (备用)
          4. 所有方式都失败 → 返回友好错误

        O5: uses shared httpx.Client for connection pooling.
        """

        # ── 尝试 1: DuckDuckGo Lite (首选) ──
        for attempt in range(MAX_RETRIES):
            result = self._search_duckduckgo_lite(query, max_results)
            if result is not None:
                return result
            # 失败后等 1s 再重试
            if attempt < MAX_RETRIES - 1:
                import time as _time
                _time.sleep(1.0)

        # ── 尝试 2: 备用搜索引擎 (Bing HTML) ──
        result = self._search_bing_fallback(query, max_results)
        if result is not None:
            return result

        # ── 全部失败 ──
        return ToolResult(
            success=False,
            output=(
                "[ERROR: web_search is currently unavailable. "
                "All search engines (DuckDuckGo, Bing) failed to respond. "
                "Check your network connection or try again later. "
                f"Timeout: {SEARCH_TIMEOUT}s per attempt.]"
            ),
            error="all search engines failed",
        )

    def _search_duckduckgo_lite(self, query: str, max_results: int) -> ToolResult | None:
        """DuckDuckGo Lite 搜索. 返回 None 表示需要重试/降级。"""
        import httpx
        url = "https://lite.duckduckgo.com/lite/"
        try:
            client = _get_search_http_client()
            resp = client.post(url, data={"q": query})
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError):
            return None
        except Exception:
            return None

        if resp.status_code != 200:
            return None

        html = resp.text

        # 解析 DuckDuckGo Lite 结果
        try:
            results = self._parse_lite_results(html, max_results)
        except ImportError:
            results = []

        if not results:
            try:
                results = self._search_fallback(query, max_results)
            except ImportError:
                return None

        if not results:
            return self._format_empty_results(query)

        return self._format_results(query, results)

    def _search_bing_fallback(self, query: str, max_results: int) -> ToolResult | None:
        """备用搜索引擎: Bing HTML 搜索 (无需 API key)。
        
        当 DuckDuckGo 不可用时自动降级到此方法。
        """
        url = "https://www.bing.com/search"
        try:
            client = _get_search_http_client()
            resp = client.get(url, params={"q": query}, timeout=FALLBACK_TIMEOUT)
        except Exception:
            return None

        if resp.status_code != 200:
            return None

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[dict[str, str]] = []

        # Bing 结果结构: <li class="b_algo"> 包含 <h2><a> 标题 + <p> 摘要
        for li in soup.find_all("li", class_="b_algo"):
            if len(results) >= max_results:
                break
            title = ""
            url = ""
            snippet = ""

            h2 = li.find("h2")
            if h2:
                link = h2.find("a")
                if link:
                    title = link.get_text(strip=True)
                    href = link.get("href", "")
                    if href:
                        url = str(href)

            p = li.find("p")
            if p:
                snippet = p.get_text(strip=True)
                if len(snippet) > SNIPPET_MAX:
                    snippet = snippet[:SNIPPET_MAX] + "..."

            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})

        if not results:
            return None

        return self._format_results(query, results)

    def _format_results(self, query: str, results: list[dict[str, str]]) -> ToolResult:
        """格式化搜索结果输出。"""
        output_parts = [f"## Search Results: {query}", ""]
        for i, r in enumerate(results, 1):
            output_parts.append(f"### {i}. {r['title']}")
            if r.get("snippet"):
                output_parts.append(r["snippet"])
            output_parts.append(f"   URL: {r['url']}")
            output_parts.append("")

        output = "\n".join(output_parts).strip()
        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "query": query,
                "results_count": len(results),
                "results": results,
            },
        )

    def _format_empty_results(self, query: str) -> ToolResult:
        """格式化空结果。"""
        return ToolResult(
            success=True,
            output=f"## Search Results: {query}\n\n(no results found)",
            artifacts={
                "query": query,
                "results_count": 0,
                "results": [],
            },
        )

    def _parse_lite_results(self, html: str, max_results: int) -> list[dict[str, str]]:
        """parse DuckDuckGo Lite 页面中的search结果。

        Lite 版 HTML 结构（当前）:
          每个结果在 <div class="result"> 内, 包含标题链接和摘要文本。
          与 HTML 版结构相似但更简洁。

        备用: 如果 Lite 版结构变动, 自动降级到 _search_fallback。
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []

        # 新version Lite: <div class="result">（当前实际结构）
        result_divs = soup.find_all("div", class_="result")
        for div in result_divs:
            if len(results) >= max_results:
                break

            link = div.find("a")
            if link:
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if not title or not href:
                    continue

                # decode DuckDuckGo 跳转链接
                url = str(href)
                if "uddg=" in url:
                    import re as _re
                    m = _re.search(r'uddg=([^&]+)', url)
                    if m:
                        from urllib.parse import unquote as _unquote
                        url = _unquote(m.group(1))

                # digest在 <div class="snippet"> 或紧随的文本节点中
                snippet = ""
                snippet_div = div.find("div", class_="snippet")
                if snippet_div:
                    snippet = snippet_div.get_text(strip=True)
                else:
                    # fallback: 取 div 内非链接的文本
                    for child in div.children:
                        txt = getattr(child, "get_text", lambda: str(child))()
                        txt = txt.strip()
                        if txt and txt != title:
                            snippet = txt
                            break

                if len(snippet) > SNIPPET_MAX:
                    snippet = snippet[:SNIPPET_MAX] + "..."

                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })

        # 如果 Lite parse没找到结果, downgrade到 fallback
        if not results:
            return self._search_fallback_lite(html, max_results)

        return results

    def _search_fallback_lite(self, html: str, max_results: int) -> list[dict[str, str]]:
        """备用 Lite parse: 基于 <table> 的旧version结构。

        保留以兼容可能的 Lite 版本回退。
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []
        result_tables = soup.find_all("table", class_="result")
        for table in result_tables:
            if len(results) >= max_results:
                break

            title = ""
            url = ""
            snippet = ""

            header = table.find("tr", class_="result-header")
            if header:
                link = header.find("a")
                if link:
                    title = link.get_text(strip=True)
                    href = link.get("href", "")
                    if href:
                        url = str(href)

            snippet_row = table.find("tr", class_="result-snippet")
            if snippet_row:
                snippet = snippet_row.get_text(strip=True)
                if len(snippet) > SNIPPET_MAX:
                    snippet = snippet[:SNIPPET_MAX] + "..."

            if title and url:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })

        return results

    def _search_fallback(self, query: str, max_results: int) -> list[dict[str, str]]:
        """备用search方式: 使用 DuckDuckGo HTML version (非 Lite)。

        当 Lite 版解析失败时使用此方式。
        O5: uses shared httpx.Client for connection pooling.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError("beautifulsoup4 is required for search fallback")

        url = "https://html.duckduckgo.com/html/"
        try:
            client = _get_search_http_client()
            resp = client.post(url, data={"q": query})
        except Exception:
            return []

        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[dict[str, str]] = []

        # HTML version结果: 每个结果在 <div class="result"> 内
        for div in soup.find_all("div", class_="result"):
            if len(results) >= max_results:
                break

            title = ""
            url = ""
            snippet = ""

            link = div.find("a", class_="result__a")
            if link:
                title = link.get_text(strip=True)
                href = link.get("href", "")
                # DuckDuckGo 的跳转链接需要提取真实 URL
                if isinstance(href, str) and "uddg=" in href:
                    m = re.search(r'uddg=([^&]+)', href)
                    if m:
                        url = unquote(m.group(1))
                else:
                    url = str(href)

            snippet_div = div.find("a", class_="result__snippet")
            if snippet_div:
                snippet = snippet_div.get_text(strip=True)
                if len(snippet) > SNIPPET_MAX:
                    snippet = snippet[:SNIPPET_MAX] + "..."

            if title and url:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })

        return results