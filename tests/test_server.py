from __future__ import annotations

import http.client
import json
import threading

import pytest
import requests

from vidpipe import cli, doctor, server
from vidpipe.errors import BadInput


class Running:
    def __init__(self, httpd):
        self.httpd = httpd
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(self, path, **kw):
        kw.setdefault("allow_redirects", False)
        return requests.get(self.base + path, timeout=10, **kw)

    def post(self, path, **kw):
        kw.setdefault("allow_redirects", False)
        return requests.post(self.base + path, timeout=10, **kw)

    def raw_get(self, path, host=None):
        """不經過 requests 的路徑正規化，才測得到 `..`。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("GET", path, skip_host=host is not None)
        if host is not None:
            conn.putheader("Host", host)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    root = tmp_path / "pkg" / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>vidpipe</title>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "pkg" / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    (tmp_path / "pkg" / "static-evil").mkdir()
    (tmp_path / "pkg" / "static-evil" / "x.txt").write_text("EVIL", encoding="utf-8")
    monkeypatch.setattr(server, "STATIC_DIR", root)
    return root


@pytest.fixture
def start(static_dir):
    running: list[Running] = []

    def _start(token: str = "", host: str = "127.0.0.1"):
        r = Running(server.make_server(host, 0, token=token))
        running.append(r)
        return r

    yield _start
    for r in running:
        r.httpd.shutdown()
        r.httpd.server_close()


def _err(resp):
    body = resp.json()
    assert body["ok"] is False
    return body["error"]


# ------------------------------------------------------------ 基本

def test_version_is_public_and_json(start):
    srv = start(token="s3cret")
    body = srv.get("/api/version").json()
    assert body["ok"] and body["auth_required"] is True and body["version"] and body["build"]
    assert start().get("/api/version").json()["auth_required"] is False


def test_shell_and_static_files(start):
    srv = start()
    page = srv.get("/")
    assert page.status_code == 200 and "<title>vidpipe</title>" in page.text
    assert "default-src 'none'" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    js = srv.get("/static/assets/app.js")
    assert js.status_code == 200 and js.headers["Content-Type"].startswith("text/javascript")
    assert srv.get("/static/assets/app.css").headers["Content-Type"].startswith("text/css")
    assert srv.get("/static/assets/missing.js").status_code == 404


def test_missing_frontend_build_is_a_structured_error(start, static_dir):
    (static_dir / "index.html").unlink()
    resp = start().get("/")
    assert resp.status_code == 503
    assert _err(resp)["code"] == "dependency_missing" and "npm run build" in _err(resp)["hint"]


def test_unknown_routes(start):
    srv = start()
    api = srv.get("/api/nope")
    assert api.status_code == 404 and _err(api)["code"] == "not_found"  # /api/ 一律 JSON
    plain = srv.get("/nope")
    assert plain.status_code == 404 and plain.headers["Content-Type"].startswith("text/plain")


def test_static_cannot_escape_its_directory(start):
    srv = start()
    for path in ("/static/../secret.txt", "/static/..%2Fsecret.txt",
                 "/static/../static-evil/x.txt", "/static/assets/../../secret.txt"):
        status, body = srv.raw_get(path)
        assert status == 404 and b"SECRET" not in body and b"EVIL" not in body, path


# ------------------------------------------------------------ 認證

def test_token_protects_api_but_not_the_shell(start):
    srv = start(token="s3cret")
    assert srv.get("/").status_code == 200
    assert srv.get("/static/assets/app.js").status_code == 200
    denied = srv.get("/api/config")
    assert denied.status_code == 401 and _err(denied)["code"] == "unauthorized"
    assert denied.headers["WWW-Authenticate"].startswith("Bearer")
    assert srv.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert srv.get("/api/config", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_login_cookie_flow(start):
    srv = start(token="s3cret")
    wrong = srv.get("/?token=nope")
    assert wrong.status_code == 200 and "Set-Cookie" not in wrong.headers  # 不洩漏對錯
    ok = srv.get("/?token=s3cret")
    assert ok.status_code == 302 and ok.headers["Location"] == "/"
    cookie = ok.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert srv.get("/api/config", cookies={"vidpipe_token": "s3cret"}).status_code == 200
    assert srv.get("/api/config", cookies={"vidpipe_token": "bad"}).status_code == 401


def test_no_token_means_open(start):
    assert start().get("/api/config").status_code == 200


# ------------------------------------------------------------ Host 與 Origin

def test_host_header_allowlist_blocks_dns_rebinding(start, monkeypatch):
    srv = start()
    for ok_host in ("localhost:8320", "127.0.0.1", "[::1]:8320", "192.168.1.5:8320"):
        assert srv.get("/api/version", headers={"Host": ok_host}).status_code == 200, ok_host
    for bad_host in ("evil.example.com", "evil.example.com:8320", "127.0.0.1.evil.com"):
        resp = srv.get("/api/version", headers={"Host": bad_host})
        assert resp.status_code == 403 and _err(resp)["code"] == "forbidden", bad_host
    monkeypatch.setenv("VIDPIPE_ALLOWED_HOSTS", "vidpipe.lan, other.lan")
    allowed = start()
    assert allowed.get("/api/version", headers={"Host": "vidpipe.lan:8320"}).status_code == 200
    assert allowed.get("/api/version", headers={"Host": "evil.example.com"}).status_code == 403


def test_bind_host_is_allowed_by_name(start):
    httpd = server.make_server("localhost", 0, token="")
    try:
        assert "localhost" not in httpd.allowed_hosts  # 本機名稱本來就放行
    finally:
        httpd.server_close()
    assert server.host_allowed("myhost.lan", frozenset({"myhost.lan"}))
    assert not server.host_allowed("myhost.lan")


def test_writes_require_same_origin(start):
    srv = start()
    base_host = f"127.0.0.1:{srv.port}"
    for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"},
                    {"Referer": "http://evil.example/page"},
                    {"Origin": f"http://localhost:{srv.port}"}):  # 不同源（Host 是 127.0.0.1）
        resp = srv.post("/api/anything", headers=headers)
        assert resp.status_code == 403 and _err(resp)["code"] == "forbidden", headers
    # 同源或沒帶 Origin（curl、本機程式）→ 通過防護，走到路由才 404
    for headers in ({"Origin": f"http://{base_host}"}, {"Referer": f"http://{base_host}/x"}, {}):
        resp = srv.post("/api/anything", headers=headers)
        assert resp.status_code == 404 and _err(resp)["code"] == "not_found", headers
    for method in ("put", "delete"):
        resp = requests.request(method, srv.base + "/api/anything", timeout=10,
                                headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403, method


def test_writes_check_auth_before_origin(start):
    srv = start(token="s3cret")
    assert srv.post("/api/anything").status_code == 401
    resp = srv.post("/api/anything", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 404


def test_reads_do_not_check_origin(start):
    # 讀取是安全方法；跨站頁面讀不到回應（沒有 CORS 標頭），不必擋
    assert start().get("/api/version", headers={"Origin": "http://evil.example"}).status_code == 200


# ------------------------------------------------------------ 啟動把關

def test_check_bind_rules():
    server.check_bind("127.0.0.1", allow_lan=False, token="", no_auth=False)
    server.check_bind("localhost", allow_lan=False, token="", no_auth=False)
    with pytest.raises(BadInput, match="不預設對外開放") as lan:
        server.check_bind("0.0.0.0", allow_lan=False, token="x", no_auth=False)
    assert "--allow-lan" in lan.value.hint
    with pytest.raises(BadInput, match="token") as tok:
        server.check_bind("0.0.0.0", allow_lan=True, token="", no_auth=False)
    assert "VIDPIPE_API_TOKEN" in tok.value.hint
    server.check_bind("0.0.0.0", allow_lan=True, token="x", no_auth=False)
    server.check_bind("0.0.0.0", allow_lan=True, token="", no_auth=True)


def test_cli_serve_refuses_unsafe_bind(capsys):
    assert cli.main(["serve", "--host", "0.0.0.0", "--no-open"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_input"
    assert cli.main(["serve", "--host", "0.0.0.0", "--allow-lan", "--no-open"]) == 2
    assert "token" in json.loads(capsys.readouterr().out)["error"]["message"]


# ------------------------------------------------------------ 資料端點

def test_config_masks_secrets(start, monkeypatch):
    monkeypatch.setenv("VIDPIPE_TTS_TOKEN", "tts-secret-123")
    monkeypatch.setenv("VIDPIPE_VIDGRAB_TOKEN", "vg-secret-456")
    srv = start(token="api-secret-789")
    resp = srv.get("/api/config", headers={"Authorization": "Bearer api-secret-789"})
    assert resp.status_code == 200
    for secret in ("tts-secret-123", "vg-secret-456", "api-secret-789"):
        assert secret not in resp.text
    body = resp.json()
    assert body["config"]["tts_token"] == "***" and body["config"]["tts_model"] == ""


def test_bad_config_is_a_structured_500(start, tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_CONFIG", str(bad))
    resp = start().get("/api/config")
    assert resp.status_code == 500 and _err(resp)["code"] == "bad_config"


def test_doctor_endpoint_matches_cli_result(start, fake_server, monkeypatch):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health"
        else (200, {"data": [{"id": "m"}]}) if p == "/v1/models"
        else (200, {"version": "1"}))
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", "work")
    monkeypatch.setenv("VIDPIPE_OUT_DIR", "out")
    monkeypatch.setattr(doctor, "check_binary", lambda n: doctor._check(n, True, "fake"))
    monkeypatch.setattr(doctor, "check_subtitles_filter",
                        lambda c: doctor._check("ffmpeg_subtitles", True))
    body = start().get("/api/doctor").json()
    names = {c["name"]: c for c in body["checks"]}
    assert body["ok"] is True and names["vidgrab"]["ok"] and names["tts"]["ok"]
    skipped = start().get("/api/doctor?skip=vidgrab,tts").json()
    assert "tts" not in {c["name"] for c in skipped["checks"]}
    assert skipped["skipped"] == ["tts", "vidgrab"]


def test_doctor_reports_unreachable_services_without_failing_the_request(start, monkeypatch):
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", "http://127.0.0.1:1")
    body = start().get("/api/doctor?skip=ffmpeg,ffprobe,ffmpeg_subtitles,cjk_font").json()
    names = {c["name"]: c for c in body["checks"]}
    assert body["ok"] is False
    assert names["vidgrab"]["ok"] is False and names["tts"]["ok"] is False


def test_doctor_rejects_unknown_skip(start):
    resp = start().get("/api/doctor?skip=nope")
    assert resp.status_code == 400 and _err(resp)["code"] == "bad_input"
