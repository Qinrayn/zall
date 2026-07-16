"""zall.tools.web_fetch — Web fetch tool (ACI design).

Corresponds to:
  §4.2   工具扩展: web_fetch — 基础网页爬取能力
  Design: 模型可获取网页内容, 如同读文件

ACI Design notes:
  - 返回 markdown 化内容 (HTML → text, 模型更容易理解)
  - 自动截断超过 MAX_CHARS 的内容 (prevents context pollution)
  - 超时自动失败 (不阻塞 agent loop)
  - 错误友好 (网络/HTTP/解析错误分别提示, 不抛异常)
  - 通过 httpx 实现 (不引入 playright/puppeteer 等重型依赖)
  - v0.0.6 fix (C2): SSRF 防护 — 阻止私有 IP/元数据端点/内部主机
  - v0.0.6 fix (C3): 流式读取 — 防止 OOM, 在 MAX_RESPONSE_BYTES 处截断

IPR constraints:
  IPR-0: invariant tests at tests/test_web_fetch_invariants.py
  IPR-1: 对应 DESIGN.md §4.2 工具扩展
  IPR-3: only stdlib + httpx + beautifulsoup4, no model SDK
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from zall.core.tool import ToolResult

# 单次最大字符数 (超过此数truncate, prevents context pollution)
MAX_CHARS = 10000
# 最大response大小 (bytes)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB
# defaulttimeout
DEFAULT_TIMEOUT = 15.0

# O5: shared httpx.Client for connection pooling across _fetch calls
_HTTP_CLIENT: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    """Lazily create and return the shared httpx.Client (connection pooling)."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        import httpx
        _HTTP_CLIENT = httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; zall/1.0; +https://github.com/zall)"
                ),
            },
        )
    return _HTTP_CLIENT


def close_http_client() -> None:
    """Close the shared httpx.Client (call at shutdown)."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        try:
            _HTTP_CLIENT.close()
        except Exception:
            pass
        _HTTP_CLIENT = None


# SSRF 阻止list: 私有 IP range + 元数据端点 + 内部主机名
_SSRF_BLOCKED_NETWORKS = [
    # IPv4 私有/内部
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    # IPv6 私有/内部 (v0.1.1 fix: SSRF bypass)
    ipaddress.ip_network("::1/128"),          # loopback
    ipaddress.ip_network("fc00::/7"),         # unique-local (ULA)
    ipaddress.ip_network("fe80::/10"),        # link-local
    ipaddress.ip_network("ff00::/8"),         # multicast
    ipaddress.ip_network("::ffff:0:0/96"),    # IPv4-mapped IPv6 (防绕过)
    ipaddress.ip_network("64:ff9b::/96"),     # NAT64
    ipaddress.ip_network("100::/64"),         # discard-only
]
_SSRF_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
}


def _is_ssrf_blocked(url: str) -> tuple[bool, str]:
    """Check if URL targets a private/internal/cloud-metadata endpoint.

    Returns (is_blocked, reason).
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return False, ""

        # Check hostname blocklist
        hostname_lower = hostname.lower()
        if hostname_lower in _SSRF_BLOCKED_HOSTNAMES:
            return True, f"hostname '{hostname}' is blocked (SSRF protection)"

        # Check if hostname resolves to a blocked IP
        try:
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            # Not an IP literal — resolve DNS (try IPv4 first, then IPv6)
            resolved = False
            # 优先 IPv4 (gethostbyname 保持向后compatible)
            try:
                addr = ipaddress.ip_address(socket.gethostbyname(hostname))
                resolved = True
            except (socket.gaierror, OSError):
                pass
            # 若 IPv4 parse失败或无结果, 尝试 IPv6 (v0.1.1 fix: SSRF bypass)
            if not resolved:
                try:
                    addrs = socket.getaddrinfo(hostname, None, socket.AF_INET6)
                    if addrs:
                        addr = ipaddress.ip_address(addrs[0][4][0])
                        resolved = True
                except (socket.gaierror, OSError):
                    pass
            if not resolved:
                return False, ""

        for network in _SSRF_BLOCKED_NETWORKS:
            if addr in network:
                return True, f"IP {addr} is blocked (private/internal network, SSRF protection)"

        return False, ""
    except Exception:
        # Fail-safe: if we can't validate, block
        return True, "URL validation failed (SSRF protection)"


class WebFetchTool:
    """Web fetch tool — 爬取网页content并转为 markdown 格式。

    ACI design decisions:
      - 自动 HTML → 文本转换 (模型不需要处理 HTML 标签)
      - 返回结构化摘要 (title + 内容 + 链接)
      - 超过 MAX_CHARS 截断 → 不让模型一次吞下整个网页
      - 仅支持 HTTP(S), 不处理 JavaScript 渲染 (保持轻量)

    schema 设计:
      url:       必填, 网页 URL
      max_chars: 可选, 最大返回字符数 (默认 5000, 最大 10000)
    """

    __test__ = False

    @property
    def tool_id(self) -> str:
        return "web_fetch"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "Fetch a web page and extract its text content. "
                    "Returns the page title and main text content in markdown format. "
                    "Useful for reading documentation, API references, news articles, "
                    "and other web-based information. "
                    f"Auto-truncates at {MAX_CHARS} characters with a notice."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to fetch (must start with http:// or https://)",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": f"Maximum characters to return (default: 5000, max: {MAX_CHARS})",
                            "default": 5000,
                        },
                    },
                    "required": ["url"],
                },
            },
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        url = args.get("url", "")
        if not url:
            return ToolResult(
                success=False,
                output="[ERROR: url argument is required]",
                error="url is required",
            )

        # validate URL 格式
        if not url.startswith(("http://", "https://")):
            return ToolResult(
                success=False,
                output="[ERROR: URL must start with http:// or https://]",
                error="invalid URL scheme",
            )

        # v0.0.6 fix (C2): SSRF 防护 — check私有 IP/元数据端点
        is_blocked, block_reason = _is_ssrf_blocked(url)
        if is_blocked:
            return ToolResult(
                success=False,
                output=f"[ERROR: {block_reason}]",
                error="SSRF blocked",
            )

        max_chars = args.get("max_chars", 5000)
        if not isinstance(max_chars, int) or max_chars < 1:
            max_chars = 5000
        max_chars = min(max_chars, MAX_CHARS)

        return self._fetch(url, max_chars)

    def _fetch(self, url: str, max_chars: int) -> ToolResult:
        """Fetch URL and convert to text.
        
        v0.0.6 fix (C3): 流式读取, 防止 OOM — 在 MAX_RESPONSE_BYTES 处截断,
        不再预加载完整 resp.text 后再切片。
        v0.0.6 fix (C2): 每次重定向后检查 SSRF 阻止列表。
        
        O5: uses shared httpx.Client for connection pooling.
        """
        import httpx
        from bs4 import BeautifulSoup

        try:
            client = _get_http_client()
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: failed to create HTTP client: {e}]",
                error=str(e),
            )

# H1: 只允许 http/https scheme (防 file:/// gopher:// 等 scheme bypass)
        _SAFE_SCHEMES = frozenset({"http", "https"})

        # 手动重定向循环 (最多 5 次), 每次 check SSRF + scheme
        redirect_count = 0
        max_redirects = 5
        current_url = url

        while redirect_count < max_redirects:
            # H1: 验证 URL scheme (每次重定向后重新验证)
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(current_url)
            if parsed.scheme.lower() not in _SAFE_SCHEMES:
                return ToolResult(
                    success=False,
                    output=f"[ERROR: unsafe URL scheme '{parsed.scheme}' blocked. Only http/https allowed.]",
                    error="unsafe URL scheme",
                )

            # H1: SSRF check 在请求前 + 请求后双重验证 (缩小 TOCTOU 窗口)
            is_blocked, block_reason = _is_ssrf_blocked(current_url)
            if is_blocked:
                return ToolResult(
                    success=False,
                    output=f"[ERROR: {block_reason}]",
                    error="SSRF blocked",
                )

            try:
                resp = client.get(current_url)
            except httpx.ConnectError:
                return ToolResult(
                    success=False,
                    output=f"[ERROR: cannot connect to {current_url}. Check the URL or your network connection.]",
                    error="connection error",
                )
            except httpx.TimeoutException:
                return ToolResult(
                    success=False,
                    output=f"[ERROR: timeout fetching {current_url} (>{DEFAULT_TIMEOUT}s). The page may be too slow or unreachable.]",
                    error="timeout",
                )
            except httpx.InvalidURL:
                return ToolResult(
                    success=False,
                    output=f"[ERROR: invalid URL: {current_url}]",
                    error="invalid URL",
                )
            except Exception as e:
                return ToolResult(
                    success=False,
                    output=f"[ERROR: failed to fetch {current_url}: {e}]",
                    error=str(e),
                )

            # H1: 请求后再次验证 SSRF (防 DNS rebinding)
            is_blocked2, block_reason2 = _is_ssrf_blocked(current_url)
            if is_blocked2:
                resp.close()
                return ToolResult(
                    success=False,
                    output=f"[ERROR: {block_reason2}]",
                    error="SSRF blocked (post-request)",
                )

            if resp.status_code == 200:
                break  # success
            elif resp.status_code in (301, 302, 303, 307, 308):
                redirect_count += 1
                location = resp.headers.get("location", "")
                if not location:
                    resp.close()
                    return ToolResult(
                        success=False,
                        output=f"[ERROR: redirect without Location header for {current_url}]",
                        error="missing redirect location",
                    )
                from urllib.parse import urljoin
                current_url = urljoin(current_url, location)
                resp.close()
                continue
            else:
                resp.close()
                return ToolResult(
                    success=False,
                    output=f"[ERROR: HTTP {resp.status_code} for {current_url}]",
                    error=f"HTTP {resp.status_code}",
                )

        if redirect_count >= max_redirects:
            resp.close()
            return ToolResult(
                success=False,
                output=f"[ERROR: too many redirects for {url}]",
                error="too many redirects",
            )

        # check Content-Type
        content_type = resp.headers.get("content-type", "")
        if "text/" not in content_type and "application/json" not in content_type:
            resp.close()
            return ToolResult(
                success=False,
                output=f"[ERROR: unsupported content type '{content_type}' for {current_url}. "
                       f"web_fetch only supports text and JSON content.]",
                error="unsupported content type",
            )

        # v0.0.6 fix (C3): stream式read, 逐块累积, 在 MAX_RESPONSE_BYTES 处truncate
        content_parts: list[str] = []
        total_bytes = 0
        truncated = False
        chunk = b""
        try:
            for chunk in resp.iter_bytes(chunk_size=8192):
                if total_bytes + len(chunk) > MAX_RESPONSE_BYTES:
                    remaining = MAX_RESPONSE_BYTES - total_bytes
                    if remaining > 0:
                        content_parts.append(chunk[:remaining].decode("utf-8", errors="replace"))
                    truncated = True
                    break
                content_parts.append(chunk.decode("utf-8", errors="replace"))
                total_bytes += len(chunk)
        finally:
            resp.close()

        content = "".join(content_parts)
        if truncated:
            content += (
                f"\n\n[Note: response was truncated at {MAX_RESPONSE_BYTES} bytes "
                f"(total response size exceeded limit)]"
            )

        # HTML → 文本convert
        try:
            soup = BeautifulSoup(content, "html.parser")
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"[ERROR: failed to parse HTML: {e}]",
                error=str(e),
            )

        # 提取title
        title = ""
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()

        # remove脚本/样式
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                         "noscript", "svg", "form", "button", "iframe"]):
            tag.decompose()

        # 提取文本, preserve结构
        lines: list[str] = []
        for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p",
                                      "li", "pre", "code", "blockquote", "hr",
                                      "br", "td", "th"]):
            tag_name = element.name
            text = element.get_text(strip=True)
            if not text:
                continue

            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                prefix = "#" * int(tag_name[1]) + " "
                lines.append(f"\n{prefix}{text}\n")
            elif tag_name == "li":
                lines.append(f"  - {text}")
            elif tag_name == "pre":
                code = element.get_text()
                lines.append(f"\n```\n{code}\n```\n")
            elif tag_name == "code":
                lines.append(f"`{text}`")
            elif tag_name == "blockquote":
                lines.append(f"> {text}")
            elif tag_name == "hr":
                lines.append("\n---\n")
            elif tag_name == "br":
                lines.append("\n")
            elif tag_name in ("td", "th"):
                lines.append(f"| {text} ")
            else:
                lines.append(text)

        # 提取链接
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            link_text = a.get_text(strip=True)
            if href and link_text and not href.startswith("#") and not href.startswith("javascript:"):  # type: ignore[union-attr]
                links.append(f"  - [{link_text}]({href})")

        # buildoutput
        output_parts = []
        if title:
            output_parts.append(f"# {title}")

        output_parts.append(f"Source: {url}")
        raw_text = "\n".join(lines)

        # cleanup多余空白
        import re
        raw_text = re.sub(r'\n{3,}', '\n\n', raw_text)
        raw_text = raw_text.strip()

        if raw_text:
            output_parts.append(raw_text)

        if links:
            link_limit = min(len(links), 20)  # 最多 20 个链接
            output_parts.append(f"\n**Links ({len(links)} found, showing {link_limit}):**")
            output_parts.extend(links[:link_limit])

        output = "\n".join(output_parts)

        # truncate
        if len(output) > max_chars:
            output = output[:max_chars] + (
                f"\n\n[... truncated at {max_chars} characters. "
                f"Original content was {len(output)} chars. "
                f"Use a larger max_chars or fetch specific sections.]"
            )

        return ToolResult(
            success=True,
            output=output,
            artifacts={
                "url": url,
                "title": title,
                "chars": len(output),
                "links_found": len(links),
            },
        )