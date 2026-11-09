"""網頁伺服器：標準庫 `ThreadingHTTPServer`，做法跟 vidgrab 一致。

同一支程式提供網頁外殼、`/static/*`、REST API（JSON）、`/file/*`（成品與中間檔，支援 Range）。
`POST /mcp` 是 MCP（Streamable HTTP）的入口，工具實作在 `mcp.py`，認證與 `/api/*` 相同。

安全（這個服務會呼叫 TTS、讀寫 work 目錄，不能裸奔）：
- 預設只綁 127.0.0.1。綁到別的位址必須明講 `--allow-lan`，而且要設 `VIDPIPE_API_TOKEN`
  （或明講 `--no-auth`），否則拒絕啟動。
- 設了 token：`/api/*` 與 `/file/*` 要 `Authorization: Bearer`，或先開一次 `/?token=…`
  換一個 HttpOnly、SameSite=Strict 的 cookie（`<img>`／`<video>` 帶不了 header）。
  頁面外殼、`/static/*`、`/api/version`（healthcheck）公開。
- **Host 標頭白名單**，擋 DNS rebinding：只認本機名稱、IP 字面值（rebinding 靠網域名稱，
  IP 字面值騙不了）、以及 `VIDPIPE_ALLOWED_HOSTS` 列出的名稱。
- **寫入類請求（POST／PUT／DELETE）檢查 Origin**，擋 CSRF：帶了 Origin／Referer 就必須跟
  Host 同源；沒帶（curl、本機程式）放行。
- 祕密（`tts_token`、`vidgrab_token`、API token）不出現在任何回應。
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import jobs as jobs_mod
from . import mcp as mcp_mod
from . import shorts as shorts_mod
from .errors import BadInput, VidpipeError, from_exception
from .version import __version__, build, full_version

TOKEN_ENV = "VIDPIPE_API_TOKEN"
TOKEN_COOKIE = "vidpipe_token"
DEFAULT_PORT = 8320
STATIC_DIR = Path(__file__).with_name("static")
MAX_BODY = 4 * 1024 * 1024      # 一份 manifest 不會超過這麼大
FILE_CHUNK = 1 << 20

# 不需要 token 的路徑：外殼、版本（Docker healthcheck 用）；/static/ 另外判斷
PUBLIC_ROUTES = ("/", "/index.html", "/api/version")
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")

# 結構化錯誤 code ↔ HTTP 狀態（跟 CLI 的 code 同一套）
ERROR_STATUS = {
    "bad_input": 400, "unauthorized": 401, "forbidden": 403, "not_found": 404,
    "method_not_allowed": 405, "conflict": 409, "stale_manifest": 409, "clip_too_short": 400,
    "cancelled": 409,
    "upstream_error": 502, "dependency_missing": 503, "timeout": 504,
    "bad_config": 500, "render_failed": 500, "internal": 500,
}
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2",
    ".map": "application/json",
}
# 外殼的 CSP：只許同源資源，唯一例外是圖片可以是 https（換素材時的遠端縮圖，素材站的 CDN 網址
# 不固定）。圖片執行不了程式；`<img>` 一律帶 no-referrer。
# React 用 CSSOM 設樣式，不受 style-src 影響。
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self'; "
       "font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def api_token() -> str:
    """API token；空字串＝不啟用認證（只適合綁本機）。只讀環境變數，不放設定檔。"""
    return (os.environ.get(TOKEN_ENV) or "").strip()


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_NAMES or host == "[::1]"


def check_bind(host: str, *, allow_lan: bool, token: str, no_auth: bool) -> None:
    """綁非本機位址前的把關。不合格丟 BadInput，呼叫端 exit 2。"""
    if is_loopback(host):
        return
    if not allow_lan:
        raise BadInput(
            f"不預設對外開放：{host} 不是本機位址",
            hint="確定要讓區網連進來就加 --allow-lan，並設 VIDPIPE_API_TOKEN")
    if not token and not no_auth:
        raise BadInput(
            "綁非本機位址必須設 API token",
            hint=f"export {TOKEN_ENV}=$(openssl rand -hex 24)；外層已經有保護、"
                 "確定要裸奔時才加 --no-auth")


def _token_matches(candidate: str, token: str) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate.encode(), token.encode())


def _hostname(value: str) -> str:
    """`Host` 標頭 → 主機名（去掉 port 與 IPv6 的方括號）。"""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def host_allowed(host_header: str | None, extra: frozenset[str] = frozenset()) -> bool:
    if not host_header:  # HTTP/1.0 沒有 Host；瀏覽器一定會帶，所以不會是 rebinding
        return True
    name = _hostname(host_header)
    if name in LOOPBACK_NAMES or name in extra:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def origin_allowed(headers: Any) -> bool:
    """帶了 Origin／Referer 就必須跟 Host 同源；都沒帶放行（非瀏覽器的呼叫端）。"""
    host = (headers.get("Host") or "").strip().lower()
    for name in ("Origin", "Referer"):
        value = headers.get(name)
        if not value:
            continue
        try:
            netloc = urllib.parse.urlparse(value).netloc.lower()
        except ValueError:
            return False
        return bool(netloc) and netloc == host
    return True


def status_for(exc: VidpipeError) -> int:
    return ERROR_STATUS.get(exc.code, 400)


def error_body(message: str, *, code: str, hint: str = "",
               retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message,
                                   "retryable": retryable, "hint": hint}}


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """`bytes=a-b`、`bytes=a-`、`bytes=-n`（單一範圍）→ (起, 迄)，含頭含尾；不合法回 None。"""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or (not match[1] and not match[2]) or size == 0:
        return None
    if not match[1]:                      # 最後 n 個位元組
        n = int(match[2])
        return (max(0, size - n), size - 1) if n > 0 else None
    start = int(match[1])
    end = int(match[2]) if match[2] else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


class Handler(BaseHTTPRequestHandler):
    server_version = f"vidpipe/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # 安靜
        return

    # -- 回應 ----------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: Any, code: int = 200, headers: dict[str, str] | None = None) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), headers=headers)

    def _error(self, message: str, *, status: int, code: str, hint: str = "",
               headers: dict[str, str] | None = None) -> None:
        self._json(error_body(message, code=code, hint=hint), status, headers)

    def _fail(self, exc: BaseException) -> None:
        err = from_exception(exc)
        if err.code == "internal":
            traceback.print_exc(file=sys.stderr)
        self._json(err.to_dict(), status_for(err))

    # -- 認證與防護 ----------------------------------------------------

    @property
    def _token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def _authorized(self, route: str) -> bool:
        token = self._token
        if not token or route in PUBLIC_ROUTES or route.startswith("/static/"):
            return True
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer ") and _token_matches(header[7:].strip(), token):
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == TOKEN_COOKIE and _token_matches(urllib.parse.unquote(value), token):
                return True
        return False

    def _guard(self, route: str, *, write: bool) -> bool:
        """通過回 True；否則已回應 403／401，呼叫端直接結束。"""
        extra = self.server.allowed_hosts  # type: ignore[attr-defined]
        if not host_allowed(self.headers.get("Host"), extra):
            self._error("Host 標頭不被接受", status=403, code="forbidden",
                        hint="用 localhost 或 IP 連；用網域名稱連要把它加進 VIDPIPE_ALLOWED_HOSTS")
            return False
        if not self._authorized(route):
            self._error("需要 API token", status=401, code="unauthorized",
                        hint="帶 Authorization: Bearer <token>；瀏覽器先開 /?token=<token> 登入",
                        headers={"WWW-Authenticate": 'Bearer realm="vidpipe"'})
            return False
        if write and not origin_allowed(self.headers):
            self._error("跨站請求已被拒絕", status=403, code="forbidden")
            return False
        return True

    def _login_redirect(self, query: str) -> bool:
        """`/?token=xxx`：正確就種 cookie 並導回 `/`（網頁裡的 img／video 沒辦法帶 header）。"""
        token = self._token
        given = (urllib.parse.parse_qs(query).get("token") or [""])[0]
        if not token or not _token_matches(given, token):
            return False
        self.send_response(302)
        cookie = f"{TOKEN_COOKIE}={urllib.parse.quote(token)}; Path=/; HttpOnly; SameSite=Strict"
        self.send_header("Set-Cookie", cookie)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    # -- 路由 ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        route, query = parsed.path, parsed.query
        try:
            if not self._guard(route, write=False):
                return
            if route in ("/", "/index.html"):
                if route == "/" and "token=" in query and self._login_redirect(query):
                    return
                self._shell()
            elif route.startswith("/static/"):
                self._static(route[len("/static/"):])
            elif route == "/api/version":
                self._json({"ok": True, "version": __version__, "build": build(),
                            "full": full_version(), "auth_required": bool(self._token)})
            elif route == "/api/config":
                cfg = config_mod.load()
                self._json({"ok": True, "path": str(config_mod.config_path()),
                            "config": cfg.to_dict(mask=True),
                            "vidgrab_open": config_mod.vidgrab_open_url(cfg)})
            elif route == "/api/doctor":
                self._doctor(query)
            elif route == "/api/jobs":
                self._jobs(query)
            elif route.startswith("/api/job/"):
                self._job(route[len("/api/job/"):], query)
            elif route == "/api/shorts":
                self._json({"ok": True, "shorts": shorts_mod.list_shorts(config_mod.load())})
            elif route.startswith("/api/short/"):
                self._json({"ok": True, "short": shorts_mod.detail(
                    config_mod.load(), urllib.parse.unquote(route[len("/api/short/"):]))})
            elif route.startswith("/file/"):
                self._file(route[len("/file/"):])
            elif route == "/mcp":
                self._mcp_only_post()
            elif route.startswith("/api/"):
                self._error(f"沒有這個端點：{route}", status=404, code="not_found")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001 - 一律轉成結構化錯誤
            self._fail(exc)

    def _write(self) -> None:
        """POST／PUT／DELETE 共用入口。W1 還沒有寫入類端點，但防護先到位，之後加路由不會漏。"""
        route = urllib.parse.urlparse(self.path).path
        try:
            if not self._guard(route, write=True):
                return
            parts = route.strip("/").split("/")
            if (self.command == "POST" and len(parts) == 4 and parts[:2] == ["api", "job"]
                    and parts[3] == "cancel"):
                work_dir = config_mod.load().work_dir
                self._json({"ok": True, "job": jobs_mod.request_cancel(work_dir, parts[2]),
                            "signalled": True})
            elif (self.command == "PUT" and len(parts) == 4 and parts[:2] == ["api", "short"]
                  and parts[3] == "manifest"):
                body = self._body()
                self._json({"ok": True, **shorts_mod.update_manifest(
                    config_mod.load(), urllib.parse.unquote(parts[2]), body.get("manifest"),
                    body.get("revision"))})
            elif (self.command == "POST" and len(parts) == 4 and parts[:2] == ["api", "short"]
                  and parts[3] == "search"):
                self._json({"ok": True, **shorts_mod.search_assets(
                    config_mod.load(), urllib.parse.unquote(parts[2]), self._body())})
            elif self.command == "DELETE" and len(parts) == 3 and parts[:2] == ["api", "short"]:
                self._json({"ok": True, **shorts_mod.delete(
                    config_mod.load(), urllib.parse.unquote(parts[2]))})
            elif self.command == "POST" and route == "/api/validate":
                manifest, _ = shorts_mod.parse_document(self._manifest_doc())
                self._json({"ok": True, **shorts_mod.describe(manifest)})
            elif self.command == "POST" and len(parts) == 2 and parts[0] == "api" \
                    and parts[1] in shorts_mod.OPS:
                self._start(parts[1])
            elif route == "/mcp":
                if self.command == "POST":
                    self._mcp()
                else:
                    self._mcp_only_post()
            elif route.startswith("/api/"):
                self._error(f"沒有這個端點：{route}", status=404, code="not_found")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    do_POST = do_PUT = do_DELETE = _write  # noqa: N815

    def _raw_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadInput("Content-Length 不對") from None
        if length < 0 or length > MAX_BODY:
            raise BadInput(f"請求太大（上限 {MAX_BODY // 1024 // 1024} MB）")
        return self.rfile.read(length) if length else b""

    def _body(self) -> dict[str, Any]:
        """請求本體的 JSON 物件。太大、不是 JSON、不是物件都回 400。"""
        cached = getattr(self, "_cached_body", None)
        if cached is not None:
            return cached
        raw = self._raw_body()
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise BadInput("請求本體要是 JSON") from None
        if not isinstance(data, dict):
            raise BadInput("請求本體要是 JSON 物件")
        self._cached_body = data
        return data

    def _manifest_doc(self) -> Any:
        body = self._body()
        if "manifest" not in body:
            raise BadInput("缺少 manifest", hint='本體格式：{"manifest": {…}}')
        return body["manifest"]

    def _mcp_only_post(self) -> None:
        self._error("MCP 端點只收 POST（沒有 server 主動推送的串流）", status=405,
                    code="method_not_allowed", headers={"Allow": "POST"})

    def _mcp(self) -> None:
        """POST /mcp：一則 JSON-RPC 訊息或一批。只有通知就回 202 空本體，其餘回 200 JSON。"""
        raw = self._raw_body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json(mcp_mod.rpc_error(None, -32700, "Parse error"), 400)
            return
        reply = mcp_mod.handle_payload(payload)
        if reply is None:
            self._send(202, b"", "text/plain; charset=utf-8")
        else:
            self._json(reply)

    def _start(self, op: str) -> None:
        """POST /api/<op>：{"manifest": {...}}（cover 另有 at、text；plan 也收純文字腳本）→ 202。"""
        body = self._body()
        doc = (shorts_mod.draft_from_body(body) if op == "plan" and "script" in body
               else self._manifest_doc())
        at = body.get("at")
        text = body.get("text")
        if at is not None and (isinstance(at, bool) or not isinstance(at, (int, float))):
            raise BadInput("at 要是數字（秒）")
        if text is not None and not isinstance(text, str):
            raise BadInput("text 要是字串")
        info = shorts_mod.launch(config_mod.load(), op, doc, at=at, text=text)
        self._json({"ok": True, **info}, 202)

    def _file(self, rest: str) -> None:
        """GET /file/<id>/<out|work>/<檔名>：成品與中間檔，支援單一 Range（影片可以拖拉）。"""
        parts = [urllib.parse.unquote(p) for p in rest.split("/")]
        if len(parts) != 3:
            self._error(f"沒有這個檔案：/file/{rest}", status=404, code="not_found")
            return
        path, ctype = shorts_mod.resolve_file(config_mod.load(), *parts)
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        header = self.headers.get("Range")
        if header:
            span = _parse_range(header, size)
            if span is None:
                self._error("Range 超出檔案範圍", status=416, code="bad_input",
                            headers={"Content-Range": f"bytes */{size}"})
                return
            start, end = span
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(FILE_CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _doctor(self, query: str) -> None:
        from . import doctor

        raw = (urllib.parse.parse_qs(query).get("skip") or [""])[0]
        skip = [x.strip() for x in raw.split(",") if x.strip()]
        self._json(doctor.run(config_mod.load(), skip=skip))

    def _jobs(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        try:
            limit = int((params.get("limit") or ["50"])[0])
        except ValueError:
            raise BadInput("limit 要是整數") from None
        status = (params.get("status") or [""])[0] or None
        work_dir = config_mod.load().work_dir
        self._json({"ok": True, "jobs": jobs_mod.list_jobs(
            work_dir, limit=max(1, min(limit, 200)), status=status)})

    def _job(self, rest: str, query: str) -> None:
        job_id, _, tail = rest.partition("/")
        work_dir = config_mod.load().work_dir
        job = jobs_mod.get_job(work_dir, job_id)
        if tail not in ("", "events"):
            self._error(f"沒有這個端點：/api/job/{rest}", status=404, code="not_found")
            return
        try:
            after = int((urllib.parse.parse_qs(query).get("after") or ["0"])[0])
        except ValueError:
            raise BadInput("after 要是整數") from None
        self._json({"ok": True, "job": job,
                    "events": jobs_mod.read_events(work_dir, job_id, after)})

    def _shell(self) -> None:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            self._error("找不到前端產物（static/index.html）", status=503,
                        code="dependency_missing",
                        hint="從原始碼跑的話先 `cd web && npm install && npm run build`")
            return
        self._send(200, page.read_bytes(), "text/html; charset=utf-8",
                   headers={"Content-Security-Policy": CSP, "X-Frame-Options": "DENY"})

    def _static(self, rest: str) -> None:
        """只認 STATIC_DIR 底下的檔案；用 relative_to 比目錄層級，不用字串前綴。"""
        root = STATIC_DIR.resolve()
        target = (STATIC_DIR / urllib.parse.unquote(rest)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        if not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT, *,
                token: str | None = None) -> ThreadingHTTPServer:
    """建好但不啟動的伺服器（測試用 port=0）。token 預設讀環境變數。"""
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.token = api_token() if token is None else token.strip()  # type: ignore[attr-defined]
    extra = {x.strip().lower() for x in (os.environ.get("VIDPIPE_ALLOWED_HOSTS") or "").split(",")}
    if host and not is_loopback(host) and host not in ("0.0.0.0", "::"):
        extra.add(host.lower())
    httpd.allowed_hosts = frozenset(x for x in extra if x)  # type: ignore[attr-defined]
    return httpd


def serve(host: str | None = None, port: int | None = None, *, open_browser: bool = True,
          allow_lan: bool = False, no_auth: bool = False) -> int:
    host = host or os.environ.get("VIDPIPE_HOST") or "127.0.0.1"
    port = port if port is not None else int(os.environ.get("VIDPIPE_PORT") or DEFAULT_PORT)
    no_auth = no_auth or (os.environ.get("VIDPIPE_NO_AUTH") or "") in ("1", "true", "yes")
    check_bind(host, allow_lan=allow_lan, token=api_token(), no_auth=no_auth)
    cfg = config_mod.load()
    httpd = make_server(host, port, token="" if no_auth else None)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{httpd.server_address[1]}/"
    # stdout 留給 JSON；人看的訊息走 stderr
    print(f"▸ vidpipe Web UI：{url}", file=sys.stderr)
    print(f"  work 目錄：{Path(cfg.work_dir).resolve()}", file=sys.stderr)
    if httpd.token:  # type: ignore[attr-defined]
        print("  已啟用 API token；瀏覽器先開 /?token=<token> 登入", file=sys.stderr)
    print("  Ctrl+C 結束", file=sys.stderr)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n關閉。", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0
