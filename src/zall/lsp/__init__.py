"""zall.lsp — LSP 集成 (Language Server Protocol).

Inspired by Grok Build's xai-grok-tools LSP implementation. Provides
real-time code intelligence: diagnostics, go-to-definition, completions,
and hover information through Language Server Protocol.

Architecture:
  ┌──────────────────────────────────────────────────────────────┐
  │  LspManager                                                  │
  │  ┌──────────┐  ┌──────────┐  ┌────────────────────────────┐ │
  │  │ Client   │→ │ Router   │→ │ Language Server (subproc)  │ │
  │  │ (JSONRPC)│  │ (dispatch)│  │ - pyright (Python)        │ │
  │  └──────────┘  └──────────┘  │ - typescript-language-srv  │ │
  │                              │ - rust-analyzer (Rust)     │ │
  │  ┌──────────┐  ┌──────────┐ │ - gopls (Go)              │ │
  │  │ Manager  │  │ Cache    │ └────────────────────────────┘ │
  │  │ (lifecycle)│ │ (diag)   │                                │
  │  └──────────┘  └──────────┘                                │
  └──────────────────────────────────────────────────────────────┘

Usage:
    manager = LspManager()
    manager.start_server("python", project_dir="/path/to/project")
    diags = manager.get_diagnostics("src/main.py")
    defs = manager.goto_definition("src/main.py", 10, 5)
    hover = manager.hover("src/main.py", 10, 5)
    manager.shutdown()

Supported languages:
  - Python:  pyright (npm) or pylsp (pip)
  - JavaScript/TypeScript: typescript-language-server (npm)
  - Rust: rust-analyzer
  - Go: gopls
  - Java: eclipse.jdt.ls
  - C/C++: clangd

IPR constraints:
  IPR-0: invariant tests at tests/test_lsp_invariants.py
  IPR-3: stdlib only, no model SDK
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# §1  LSP Types
# ═══════════════════════════════════════════════════════════════════


class DiagnosticSeverity(Enum):
    """诊断严重级别 (与 LSP 协议对应)。"""
    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


@dataclass(frozen=True)
class DiagnosticEntry:
    """LSP 诊断条目。"""
    file_path: str
    line: int  # 0-indexed
    column: int  # 0-indexed
    message: str
    severity: DiagnosticSeverity
    code: str = ""
    source: str = ""

    @property
    def severity_label(self) -> str:
        return {
            DiagnosticSeverity.ERROR: "error",
            DiagnosticSeverity.WARNING: "warning",
            DiagnosticSeverity.INFORMATION: "info",
            DiagnosticSeverity.HINT: "hint",
        }.get(self.severity, "unknown")

    def __str__(self) -> str:
        return (
            f"{self.file_path}:{self.line + 1}:{self.column + 1}: "
            f"{self.severity_label}: {self.message}"
        )


@dataclass(frozen=True)
class LocationLink:
    """定义/引用位置。"""
    file_path: str
    line: int  # 0-indexed
    column: int  # 0-indexed


@dataclass(frozen=True)
class HoverInfo:
    """Hover 信息。"""
    content: str
    language: str = ""


@dataclass(frozen=True)
class CompletionItem:
    """补全项。"""
    label: str
    kind: str = ""
    detail: str = ""
    documentation: str = ""


# ═══════════════════════════════════════════════════════════════════
# §2  LSP Server Config
# ═══════════════════════════════════════════════════════════════════


@dataclass
class LspServerConfig:
    """LSP 服务器配置。"""
    language: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    initialization_options: dict[str, Any] = field(default_factory=dict)


# 已知 LSP 服务器配置
KNOWN_SERVERS: dict[str, LspServerConfig] = {
    "python": LspServerConfig(
        language="python",
        command="pyright-langserver",
        args=["--stdio"],
        initialization_options={
            "typeshedPath": None,
            "pythonPlatform": "All",
        },
    ),
    "python-pylsp": LspServerConfig(
        language="python",
        command="pylsp",
        args=[],
    ),
    "typescript": LspServerConfig(
        language="typescript",
        command="typescript-language-server",
        args=["--stdio"],
        initialization_options={},
    ),
    "rust": LspServerConfig(
        language="rust",
        command="rust-analyzer",
        args=[],
    ),
    "go": LspServerConfig(
        language="go",
        command="gopls",
        args=[],
    ),
    "clangd": LspServerConfig(
        language="cpp",
        command="clangd",
        args=["--background-index"],
    ),
}


# ═══════════════════════════════════════════════════════════════════
# §3  JSON-RPC Transport
# ═══════════════════════════════════════════════════════════════════


class JsonRpcTransport:
    """JSON-RPC 传输层 — 通过 stdin/stdout 与 LSP 服务器通信。

    使用 Content-Length 头部的标准 LSP 传输协议。
    """

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        self._req_id = 0
        self._buffer = ""

    def send_request(self, method: str, params: Any = None) -> dict[str, Any]:
        """发送 JSON-RPC 请求并等待响应。"""
        self._req_id += 1
        msg = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params or {},
        }
        self._send(msg)
        return self._receive_response(self._req_id)

    def send_notification(self, method: str, params: Any = None) -> None:
        """发送 JSON-RPC 通知 (无响应)。"""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._send(msg)

    def _send(self, msg: dict[str, Any]) -> None:
        """发送消息到 LSP 服务器 stdin。"""
        content = json.dumps(msg, ensure_ascii=False)
        header = f"Content-Length: {len(content.encode('utf-8'))}\r\n\r\n"
        if self._process.stdin is not None:
            self._process.stdin.write(header + content)
            self._process.stdin.flush()

    def _receive_response(self, req_id: int, timeout: float = 10.0) -> dict[str, Any]:
        """接收指定 ID 的响应。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 尝试从 buffer 读取
            result = self._parse_buffer()
            if result is not None:
                msg_id = result.get("id")
                if msg_id == req_id:
                    return result

            # 从 stdout 读取更多数据
            if self._process.stdout is not None:
                line = self._process.stdout.read(1)
                if line:
                    self._buffer += line
                else:
                    time.sleep(0.01)

        raise TimeoutError(f"LSP request {req_id} timed out after {timeout}s")

    def _parse_buffer(self) -> dict[str, Any] | None:
        """尝试从 buffer 解析一条完整消息。"""
        # 查找 Content-Length 头
        if "Content-Length:" not in self._buffer:
            return None

        # 提取长度
        header_end = self._buffer.find("\r\n\r\n")
        if header_end == -1:
            return None

        header = self._buffer[:header_end]
        content_start = header_end + 4

        # 解析 Content-Length
        length = 0
        for hline in header.split("\r\n"):
            if hline.lower().startswith("content-length:"):
                length = int(hline.split(":", 1)[1].strip())

        # 检查是否收到完整内容
        if len(self._buffer) < content_start + length:
            return None

        content = self._buffer[content_start:content_start + length]
        self._buffer = self._buffer[content_start + length:]

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        """关闭传输。"""
        try:
            self.send_notification("exit")
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            self._process.kill()


# ═══════════════════════════════════════════════════════════════════
# §4  LSP Client
# ═══════════════════════════════════════════════════════════════════


class LspClient:
    """LSP 客户端 — 管理一个语言服务器的生命周期。"""

    def __init__(
        self,
        config: LspServerConfig,
        project_dir: str,
    ) -> None:
        self._config = config
        self._project_dir = project_dir
        self._transport: JsonRpcTransport | None = None
        self._initialized = False
        self._capabilities: dict[str, Any] = {}
        self._open_files: set[str] = set()

    def start(self) -> None:
        """启动 LSP 服务器并完成初始化。"""
        if self._initialized:
            return

        # 启动子进程
        try:
            proc = subprocess.Popen(
                [self._config.command] + self._config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **self._config.env},
                cwd=self._project_dir,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"LSP server '{self._config.command}' not found. "
                f"Install with: npm install -g {self._config.command}"
            )

        self._transport = JsonRpcTransport(proc)

        # 初始化
        result = self._transport.send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": f"file://{Path(self._project_dir).resolve().as_posix()}",
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": True},
                    "hover": {"dynamicRegistration": True},
                    "completion": {"completionItem": {"snippetSupport": True}},
                    "diagnostics": True,
                },
                "workspace": {
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                },
            },
            "initializationOptions": self._config.initialization_options,
        })

        self._capabilities = result.get("capabilities", {})

        # 发送 initialized 通知
        self._transport.send_notification("initialized", {})

        self._initialized = True

    def open_file(self, file_path: str) -> None:
        """通知服务器文件已打开。"""
        if not self._initialized:
            self.start()

        abs_path = str(Path(file_path).resolve())
        if abs_path in self._open_files:
            return

        uri = self._path_to_uri(abs_path)
        try:
            content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""

        self._transport.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self._config.language,
                "version": 1,
                "text": content,
            },
        })
        self._open_files.add(abs_path)

    def close_file(self, file_path: str) -> None:
        """通知服务器文件已关闭。"""
        abs_path = str(Path(file_path).resolve())
        if abs_path not in self._open_files:
            return

        uri = self._path_to_uri(abs_path)
        self._transport.send_notification("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })
        self._open_files.discard(abs_path)

    def change_file(self, file_path: str, content: str, version: int = 2) -> None:
        """通知服务器文件内容已变更。"""
        abs_path = str(Path(file_path).resolve())
        if abs_path not in self._open_files:
            self.open_file(file_path)

        uri = self._path_to_uri(abs_path)
        self._transport.send_notification("textDocument/didChange", {
            "textDocument": {
                "uri": uri,
                "version": version,
            },
            "contentChanges": [{
                "text": content,
            }],
        })

    # ── LSP Queries ──

    def get_diagnostics(
        self, file_path: str,
    ) -> list[DiagnosticEntry]:
        """获取文件的诊断信息。

        注意: LSP 协议中诊断是推送的 (textDocument/publishDiagnostics)。
        这里我们通过打开文件并等待诊断推送来获取。
        生产环境中应使用事件驱动的诊断收集器。
        """
        self.open_file(file_path)
        # LSP 服务器在 didOpen 后会推送诊断, 但没有标准请求-响应方式获取。
        # 这里返回空列表, 实际诊断通过 push-based 收集器管理。
        return []

    def goto_definition(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> list[LocationLink]:
        """跳转到定义。"""
        self.open_file(file_path)
        if self._transport is None:
            return []

        uri = self._path_to_uri(file_path)
        result = self._transport.send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
        })

        return self._parse_locations(result)

    def hover(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> HoverInfo | None:
        """获取 hover 信息。"""
        self.open_file(file_path)
        if self._transport is None:
            return None

        uri = self._path_to_uri(file_path)
        result = self._transport.send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
        })

        hover_data = result.get("result")
        if hover_data is None:
            return None

        contents = hover_data.get("contents", {})
        if isinstance(contents, str):
            return HoverInfo(content=contents)
        if isinstance(contents, dict):
            value = contents.get("value", "")
            lang = contents.get("language", "")
            return HoverInfo(content=value, language=lang)

        return HoverInfo(content=str(contents))

    def get_completions(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> list[CompletionItem]:
        """获取补全项。"""
        self.open_file(file_path)
        if self._transport is None:
            return []

        uri = self._path_to_uri(file_path)
        result = self._transport.send_request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
        })

        items = result.get("result", {})
        if isinstance(items, dict):
            items = items.get("items", [])

        completions = []
        for item in items:
            completions.append(CompletionItem(
                label=item.get("label", ""),
                kind=item.get("kind", ""),
                detail=item.get("detail", ""),
                documentation=item.get("documentation", ""),
            ))
        return completions

    # ── Helpers ──

    def _path_to_uri(self, path: str) -> str:
        return f"file://{Path(path).resolve().as_posix()}"

    def _parse_locations(
        self, result: dict[str, Any],
    ) -> list[LocationLink]:
        """解析 LSP 位置响应。"""
        locations = []
        data = result.get("result")

        if data is None:
            return []

        # 单个位置
        if isinstance(data, dict):
            loc = self._parse_location(data)
            if loc is not None:
                locations.append(loc)

        # 位置数组
        elif isinstance(data, list):
            for item in data:
                loc = self._parse_location(item)
                if loc is not None:
                    locations.append(loc)

        return locations

    def _parse_location(self, data: dict[str, Any]) -> LocationLink | None:
        """解析单个位置对象。"""
        # LSP Location
        if "uri" in data and "range" in data:
            uri = data["uri"]
            path = uri.replace("file://", "")
            if os.name == "nt" and path.startswith("/"):
                path = path[1:]  # "/C:/..." -> "C:/..."
            start = data["range"].get("start", {})
            return LocationLink(
                file_path=path,
                line=start.get("line", 0),
                column=start.get("character", 0),
            )

        # LocationLink
        if "targetUri" in data and "targetRange" in data:
            uri = data["targetUri"]
            path = uri.replace("file://", "")
            if os.name == "nt" and path.startswith("/"):
                path = path[1:]
            start = data["targetRange"].get("start", {})
            return LocationLink(
                file_path=path,
                line=start.get("line", 0),
                column=start.get("character", 0),
            )

        return None

    def shutdown(self) -> None:
        """关闭 LSP 服务器。"""
        if not self._initialized:
            return

        try:
            self._transport.send_request("shutdown")
        except Exception:
            pass

        try:
            self._transport.close()
        except Exception:
            pass

        self._initialized = False
        self._open_files.clear()

    def is_running(self) -> bool:
        return self._initialized and self._transport is not None

    @property
    def language(self) -> str:
        return self._config.language


# ═══════════════════════════════════════════════════════════════════
# §5  LSP Manager
# ═══════════════════════════════════════════════════════════════════


class LspManager:
    """LSP 管理器 — 管理多个语言服务器的生命周期。

    对应 Grok Build 的 LspManager。
    """

    def __init__(self, project_dir: str | None = None) -> None:
        self._project_dir = project_dir or os.getcwd()
        self._clients: dict[str, LspClient] = {}
        self._diagnostics: dict[str, list[DiagnosticEntry]] = {}
        self._file_to_language: dict[str, str] = {}

    # ── Server Management ──

    def start_server(
        self,
        language: str,
        config: LspServerConfig | None = None,
    ) -> LspClient:
        """启动指定语言的语言服务器。

        Args:
            language: 语言名称 (python/typescript/rust/go/clangd)
            config: 服务器配置, None 则使用 KNOWN_SERVERS

        Returns:
            LspClient 实例

        Raises:
            RuntimeError: 服务器命令未找到
            KeyError: 未知语言且未提供配置
        """
        if language in self._clients:
            return self._clients[language]

        if config is None:
            if language not in KNOWN_SERVERS:
                raise KeyError(
                    f"Unknown LSP language: '{language}'. "
                    f"Known: {', '.join(KNOWN_SERVERS.keys())}"
                )
            config = KNOWN_SERVERS[language]

        client = LspClient(config, self._project_dir)
        client.start()
        self._clients[language] = client
        return client

    def stop_server(self, language: str) -> None:
        """停止指定语言的语言服务器。"""
        client = self._clients.pop(language, None)
        if client is not None:
            client.shutdown()

    def shutdown_all(self) -> None:
        """停止所有语言服务器。"""
        for lang, client in list(self._clients.items()):
            try:
                client.shutdown()
            except Exception:
                pass
        self._clients.clear()
        self._diagnostics.clear()
        self._file_to_language.clear()

    def get_client(self, language: str) -> LspClient | None:
        """获取指定语言的客户端。"""
        return self._clients.get(language)

    def get_or_start_client(self, language: str) -> LspClient:
        """获取或启动客户端。"""
        if language not in self._clients:
            return self.start_server(language)
        return self._clients[language]

    # ── Language Detection ──

    _EXT_TO_LANG: dict[str, str] = {
        ".py": "python",
        ".js": "typescript",
        ".jsx": "typescript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".rs": "rust",
        ".go": "go",
        ".c": "clangd",
        ".cpp": "clangd",
        ".cc": "clangd",
        ".h": "clangd",
        ".hpp": "clangd",
    }

    def detect_language(self, file_path: str) -> str | None:
        """根据文件扩展名检测语言。"""
        ext = Path(file_path).suffix.lower()
        return self._EXT_TO_LANG.get(ext)

    def register_file(self, file_path: str) -> str | None:
        """注册文件并返回检测到的语言。"""
        lang = self.detect_language(file_path)
        if lang:
            self._file_to_language[file_path] = lang
        return lang

    # ── Queries ──

    def goto_definition(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> list[LocationLink]:
        """跳转到定义。"""
        lang = self._resolve_language(file_path)
        if lang is None:
            return []
        client = self.get_or_start_client(lang)
        return client.goto_definition(file_path, line, column)

    def hover(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> HoverInfo | None:
        """获取 hover 信息。"""
        lang = self._resolve_language(file_path)
        if lang is None:
            return None
        client = self.get_or_start_client(lang)
        return client.hover(file_path, line, column)

    def get_completions(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> list[CompletionItem]:
        """获取补全项。"""
        lang = self._resolve_language(file_path)
        if lang is None:
            return []
        client = self.get_or_start_client(lang)
        return client.get_completions(file_path, line, column)

    def get_diagnostics(
        self,
        file_path: str,
    ) -> list[DiagnosticEntry]:
        """获取文件的诊断信息。"""
        return self._diagnostics.get(file_path, [])

    def open_file(self, file_path: str) -> None:
        """打开文件 (自动检测语言并启动服务器)。"""
        lang = self.register_file(file_path)
        if lang is None:
            return
        client = self.get_or_start_client(lang)
        client.open_file(file_path)

    def change_file(self, file_path: str, content: str) -> None:
        """通知文件变更。"""
        lang = self._file_to_language.get(file_path)
        if lang is None:
            return
        client = self._clients.get(lang)
        if client is not None:
            client.change_file(file_path, content)

    def close_file(self, file_path: str) -> None:
        """关闭文件。"""
        lang = self._file_to_language.get(file_path)
        if lang is None:
            return
        client = self._clients.get(lang)
        if client is not None:
            client.close_file(file_path)
        self._file_to_language.pop(file_path, None)

    # ── Diagnostics Push Handler ──

    def handle_diagnostics(
        self,
        uri: str,
        diagnostics: list[dict[str, Any]],
    ) -> None:
        """处理 LSP 推送的诊断 (publishDiagnostics)。"""
        path = uri.replace("file://", "")
        # Windows path normalization: /C:/... -> C:/...
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        elif os.name == "nt" and path.startswith("/"):
            path = path[1:]

        entries = []
        for d in diagnostics:
            severity = DiagnosticSeverity(
                d.get("severity", DiagnosticSeverity.WARNING.value)
            )
            range_data = d.get("range", {})
            start = range_data.get("start", {})
            entries.append(DiagnosticEntry(
                file_path=path,
                line=start.get("line", 0),
                column=start.get("character", 0),
                message=d.get("message", ""),
                severity=severity,
                code=str(d.get("code", "")),
                source=d.get("source", ""),
            ))

        self._diagnostics[path] = entries

    # ── Helpers ──

    def _resolve_language(self, file_path: str) -> str | None:
        """解析文件的语言。"""
        # 检查已注册的文件
        lang = self._file_to_language.get(file_path)
        if lang is not None:
            return lang
        # 自动检测
        return self.register_file(file_path)

    @property
    def active_servers(self) -> dict[str, LspClient]:
        """当前活动的服务器。"""
        return dict(self._clients)

    @property
    def all_diagnostics(self) -> dict[str, list[DiagnosticEntry]]:
        """所有诊断信息。"""
        return dict(self._diagnostics)

    def summary(self) -> dict[str, Any]:
        """获取管理器摘要。"""
        error_count = sum(
            1 for diags in self._diagnostics.values()
            for d in diags if d.severity == DiagnosticSeverity.ERROR
        )
        warning_count = sum(
            1 for diags in self._diagnostics.values()
            for d in diags if d.severity == DiagnosticSeverity.WARNING
        )
        return {
            "active_servers": list(self._clients.keys()),
            "open_files": len(self._file_to_language),
            "diagnostics_errors": error_count,
            "diagnostics_warnings": warning_count,
        }

    def __repr__(self) -> str:
        return (
            f"LspManager(servers={list(self._clients.keys())}, "
            f"files={len(self._file_to_language)})"
        )