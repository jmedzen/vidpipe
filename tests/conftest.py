"""測試用的假服務：一個可程式化的本機 HTTP server，代替 vidgrab 與 audio.cpp。"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

Handler = Callable[[str, str, dict, bytes], tuple[int, object]]


class FakeServer:
    """route(method, path, headers, body) -> (status, payload)。

    payload 是 bytes 就原樣回，否則當 JSON。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, bytes]] = []
        self.handler: Handler = lambda *_: (404, {"error": "no route"})
        outer = self

        class H(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.calls.append((self.command, self.path, headers, body))
                status, payload = outer.handler(self.command, self.path, headers, body)
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type",
                                 "audio/wav" if isinstance(payload, bytes) else "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _serve  # noqa: N815

            def log_message(self, *a, **k) -> None:  # 安靜
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake_server():
    server = FakeServer()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """別讓開發機的 VIDPIPE_* 環境變數或 cwd 裡的 vidpipe.json 影響測試。"""
    import os

    for key in list(os.environ):
        if key.startswith("VIDPIPE_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIDPIPE_CONFIG", str(tmp_path / "none.json"))
