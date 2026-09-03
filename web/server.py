"""FutureBI Web UI 服务（零依赖，标准库 http.server）。

用法:
    python -m web.server [端口]     # 默认 8000

路由:
    GET  /              -> 前端页面
    GET  /api/health    -> 健康检查
    POST /api/query     -> 完整链路（body: {"query": "...", "principal": "..."}）
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from web.service import ensure_db, run_query

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8000

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._send_json({"status": "ok"})
        if parsed.path in ("/", "/index.html"):
            return self._send_file("index.html")
        rel = parsed.path[len("/static/") :] if parsed.path.startswith("/static/") else ""
        return self._send_file(rel)

    def do_POST(self) -> None:
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
        return self._send_json(run_query(query, principal))

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # 静默访问日志


def main() -> None:
    ensure_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"FutureBI Web UI running at http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
