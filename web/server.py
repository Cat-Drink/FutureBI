"""FutureBI Web UI 服务（零依赖，标准库 http.server）。

用法:
    python -m web.server [端口]     # 默认 8000

路由:
    GET  /              -> 前端页面
    GET  /api/health    -> 健康检查
    POST /api/query     -> 完整链路（body: {"query": "...", "principal": "..."}，
                          可选 "session_id" / "user" / "request_id"）

结构化日志：所有访问日志与业务日志均输出单行 JSON，request_id 由
X-Request-ID 请求头（或服务端生成）贯穿请求处理与审计链路。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from audit.logging import get_logger, get_request_id, set_request_context, setup_logging
from config import settings
from web.service import ensure_db, run_query

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8000

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

_access_logger = get_logger("web.access")


def _level_from_str(level: str) -> int:
    """把 LOG_LEVEL 字符串映射为 logging 级别（非法值回退 INFO）。"""
    import logging

    return getattr(logging, level.upper(), logging.INFO)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", get_request_id())
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel: str) -> None:
        path = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in path.parents and path != STATIC_DIR:
            return self._send_json({"error": "forbidden"}, 403)
        if not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", get_request_id())
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        # 每个请求入口注入结构化日志上下文（request_id 贯穿）
        set_request_context(request_id=self.headers.get("X-Request-ID"))
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._send_json({"status": "ok"})
        if parsed.path in ("/", "/index.html"):
            return self._send_file("index.html")
        rel = parsed.path[len("/static/") :] if parsed.path.startswith("/static/") else ""
        return self._send_file(rel)

    def do_POST(self) -> None:
        set_request_context(request_id=self.headers.get("X-Request-ID"))
        parsed = urlparse(self.path)
        if parsed.path != "/api/query":
            return self._send_json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json({"error": "invalid json"}, 400)
        query = str(body.get("query", "")).strip()
        principal = body.get("principal") or None
        if not query:
            return self._send_json({"error": "query is required"}, 400)
        result = run_query(
            query,
            principal,
            request_id=body.get("request_id") or self.headers.get("X-Request-ID"),
            session_id=body.get("session_id"),
            user=body.get("user"),
        )
        return self._send_json(result)

    def log_message(self, fmt: str, *args: object) -> None:
        # 结构化访问日志（替代默认 stderr 文本；request_id 已在上下文中）
        # BaseHTTPRequestHandler 以 log_message('"%s" %s %s', requestline, code, size) 调用。
        status = str(args[1]) if len(args) > 1 else "-"
        size = str(args[2]) if len(args) > 2 else "-"
        _access_logger.info(
            fmt % args,
            extra={
                "event": "http_access",
                "method": self.command,
                "path": self.path,
                "client_ip": self.client_address[0],
                "status": status,
                "size": size,
            },
        )


def main() -> None:
    setup_logging(_level_from_str(settings.LOG_LEVEL))
    ensure_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"FutureBI Web UI running at http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
