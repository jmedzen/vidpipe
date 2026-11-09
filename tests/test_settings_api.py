"""設定頁的 HTTP 端點：GET／PUT `/api/settings`、POST `/api/settings/test`。

防護（token、Host、Origin）與祕密不外洩要在端點層再驗一次：模組測了不代表路由有接對。
"""

from __future__ import annotations

import json

import pytest
from test_server import Running

from vidpipe import config as config_mod
from vidpipe import server, settings


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "conf" / "vidpipe.json"
    monkeypatch.setenv("VIDPIPE_CONFIG", str(path))
    return path


@pytest.fixture
def api():
    running: list[Running] = []

    def start(token: str = ""):
        r = Running(server.make_server("127.0.0.1", 0, token=token))
        running.append(r)
        return r

    yield start
    for r in running:
        r.httpd.shutdown()
        r.httpd.server_close()


def err(resp):
    body = resp.json()
    assert body["ok"] is False
    return body["error"]


def fields_of(resp):
    return {f["name"]: f for f in resp.json()["fields"]}


def test_get_describes_every_setting(api, cfg_file):
    resp = api().get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] and body["path"] == str(cfg_file.resolve()) and body["writable"]
    assert len(body["fields"]) == len(config_mod.SPECS)
    assert [g["id"] for g in body["groups"]][0] == "vidgrab"
    assert fields_of(resp)["target_lufs"]["max"] == -5


def test_settings_require_the_api_token(api, cfg_file):
    srv = api(token="api-secret")
    auth = {"Authorization": "Bearer api-secret"}
    assert srv.get("/api/settings").status_code == 401
    assert srv.put("/api/settings", json={"changes": {"tts_model": "x"}}).status_code == 401
    assert srv.post("/api/settings/test", json={"target": "dirs"}).status_code == 401
    assert srv.get("/api/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert srv.get("/api/settings", headers=auth).status_code == 200
    assert not cfg_file.exists()
    saved = srv.put("/api/settings", json={"changes": {"tts_model": "x"}}, headers=auth)
    assert saved.status_code == 200 and cfg_file.exists()
    ok = srv.post("/api/settings/test", json={"target": "dirs"}, headers=auth)
    assert ok.status_code == 200 and ok.json()["target"] == "dirs"


def test_writes_reject_cross_site_requests_and_foreign_hosts(api, cfg_file):
    srv = api()
    evil = {"Origin": "http://evil.example"}
    put = srv.put("/api/settings", json={"changes": {"tts_model": "x"}}, headers=evil)
    assert put.status_code == 403 and err(put)["code"] == "forbidden"
    test = srv.post("/api/settings/test", json={"target": "dirs"}, headers=evil)
    assert test.status_code == 403
    assert not cfg_file.exists()
    same = {"Origin": srv.base}
    assert srv.put("/api/settings", json={"changes": {"tts_model": "x"}},
                   headers=same).status_code == 200
    rebinding = srv.put("/api/settings", json={"changes": {}}, headers={"Host": "evil.example"})
    assert rebinding.status_code == 403
    assert srv.get("/api/settings", headers={"Host": "evil.example"}).status_code == 403


def test_put_saves_and_the_rest_of_the_app_sees_it(api, cfg_file):
    srv = api()
    first = srv.get("/api/settings").json()
    resp = srv.put("/api/settings", json={
        "revision": first["revision"],
        "changes": {"tts_model": "cosy", "subtitle_langs": "zh-TW+en", "target_lufs": -16}})
    body = resp.json()
    assert resp.status_code == 200 and body["changed"] == ["subtitle_langs", "target_lufs",
                                                           "tts_model"]
    assert fields_of(resp)["tts_model"]["source"] == "file"
    cfg = srv.get("/api/config").json()["config"]                  # 其他端點下一次請求就讀到
    assert cfg["tts_model"] == "cosy" and cfg["subtitle_langs"] == "zh-TW+en"
    assert cfg["target_lufs"] == -16
    reset = srv.put("/api/settings", json={"revision": body["revision"],
                                           "changes": {"tts_model": None}})
    assert reset.json()["changed"] == ["tts_model"]
    assert srv.get("/api/config").json()["config"]["tts_model"] == ""


def test_saved_vidgrab_public_url_drives_the_open_button(api, cfg_file):
    """總覽頁的「開啟 vidgrab」讀的就是設定頁存的那一欄；留空代表「從 vidgrab 位址推」。"""
    srv = api()
    rev = srv.get("/api/settings").json()["revision"]
    saved = srv.put("/api/settings", json={"revision": rev, "changes": {
        "vidgrab_url": "http://vidgrab:8310",
        "vidgrab_public_url": " http://192.168.1.10:8310/ "}})
    assert saved.status_code == 200
    assert json.loads(cfg_file.read_text("utf-8"))["vidgrab_public_url"] == "http://192.168.1.10:8310"
    assert srv.get("/api/config").json()["vidgrab_open"] == {
        "url": "http://192.168.1.10:8310", "source": "public", "hint": ""}
    cleared = srv.put("/api/settings", json={"revision": saved.json()["revision"],
                                             "changes": {"vidgrab_public_url": ""}})
    assert cleared.status_code == 200          # 選填的網址可以是空的（必填的 vidgrab_url 不行）
    assert srv.get("/api/config").json()["vidgrab_open"]["source"] == "none"
    for bad in ("vidgrab:8310", "ftp://x:1", "http://u:p@h:1", "http://h:1?x=1"):
        resp = srv.put("/api/settings", json={"changes": {"vidgrab_public_url": bad}})
        assert resp.status_code == 400 and "vidgrab_public_url" in err(resp)["fields"], bad


def test_put_validation_errors_name_each_field(api, cfg_file):
    srv = api()
    resp = srv.put("/api/settings", json={"changes": {
        "tts_concurrency": 99, "vidgrab_url": "nope", "tts_model": "ok"}})
    assert resp.status_code == 400
    e = err(resp)
    assert e["code"] == "bad_input" and set(e["fields"]) == {"tts_concurrency", "vidgrab_url"}
    assert "1–16" in e["fields"]["tts_concurrency"]
    assert not cfg_file.exists()                                    # 有一項不合格就整批不存
    for body in ({"changes": ["x"]}, {"changes": {}, "revision": 5}):
        bad = srv.put("/api/settings", json=body)
        assert bad.status_code == 400 and err(bad)["code"] == "bad_input"
    assert srv.put("/api/settings", data=b"{nope").status_code == 400


def test_put_stale_revision_is_a_409(api, cfg_file):
    srv = api()
    mine = srv.get("/api/settings").json()["revision"]
    srv.put("/api/settings", json={"changes": {"tts_model": "theirs"}})   # 別的視窗先存了
    resp = srv.put("/api/settings", json={"revision": mine, "changes": {"tts_model": "mine"}})
    assert resp.status_code == 409 and err(resp)["code"] == "stale_config"
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"tts_model": "theirs"}


def test_env_pinned_setting_is_refused(api, cfg_file, monkeypatch):
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", "http://pinned:1")
    srv = api()
    assert fields_of(srv.get("/api/settings"))["tts_base_url"]["locked"] is True
    resp = srv.put("/api/settings", json={"changes": {"tts_base_url": "http://x:2"}})
    assert resp.status_code == 400 and "VIDPIPE_TTS_BASE_URL" in err(resp)["fields"]["tts_base_url"]


def test_secrets_are_never_in_a_response(api, cfg_file, monkeypatch):
    monkeypatch.setenv("VIDPIPE_VIDGRAB_TOKEN", "env-secret-AAA")
    srv = api(token="api-secret-BBB")
    auth = {"Authorization": "Bearer api-secret-BBB"}
    put = srv.put("/api/settings", headers=auth, json={"changes": {"tts_token": "file-secret-CCC"}})
    get = srv.get("/api/settings", headers=auth)
    bad = srv.put("/api/settings", headers=auth,
                  json={"changes": {"tts_token": "file-secret-DDD", "tts_concurrency": 0}})
    test = srv.post("/api/settings/test", headers=auth,
                    json={"target": "vidgrab", "values": {"vidgrab_token": "typed-secret-EEE",
                                                          "vidgrab_url": "http://127.0.0.1:9"}})
    cfg = srv.get("/api/config", headers=auth)
    for resp in (put, get, bad, test, cfg):
        for secret in ("env-secret-AAA", "file-secret-CCC", "file-secret-DDD",
                       "typed-secret-EEE", "api-secret-BBB"):
            assert secret not in resp.text, (resp.url, secret)
    assert fields_of(get)["tts_token"]["is_set"] is True
    assert fields_of(get)["vidgrab_token"]["source"] == "env"


def test_broken_config_file_does_not_lock_you_out(api, cfg_file):
    cfg_file.parent.mkdir()
    cfg_file.write_text("{oops", encoding="utf-8")
    srv = api()
    assert srv.get("/api/config").status_code == 500               # 其他地方照舊報錯
    got = srv.get("/api/settings")
    assert got.status_code == 200 and got.json()["problems"][0]["code"] == "file_invalid"
    refused = srv.put("/api/settings", json={"changes": {"tts_model": "m"}})
    assert refused.status_code == 409 and cfg_file.read_text(encoding="utf-8") == "{oops"
    fixed = srv.put("/api/settings", json={"changes": {"tts_model": "m"}, "replace_invalid": True})
    assert fixed.status_code == 200 and fixed.json()["problems"] == []
    assert cfg_file.with_name(cfg_file.name + ".bak").read_text(encoding="utf-8") == "{oops"
    assert srv.get("/api/config").status_code == 200


def test_readonly_deployment(api, cfg_file, monkeypatch):
    monkeypatch.setenv(settings.READONLY_ENV, "1")
    srv = api()
    got = srv.get("/api/settings").json()
    assert got["readonly"] is True and got["writable"] is False
    resp = srv.put("/api/settings", json={"changes": {"tts_model": "x"}})
    assert resp.status_code == 403 and err(resp)["code"] == "forbidden"
    assert not cfg_file.exists()


def test_test_endpoint_runs_against_the_draft(api, cfg_file, fake_server):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health" else (200, {"data": [{"id": "cosy"}]}))
    srv = api()
    resp = srv.post("/api/settings/test", json={
        "target": "tts", "values": {"tts_base_url": fake_server.url}})
    body = resp.json()
    assert resp.status_code == 200 and body["ok"] and body["models"] == ["cosy"]
    assert body["checks"][0]["name"] == "tts" and "elapsed_ms" in body
    assert not cfg_file.exists()                                    # 測試不存設定
    unreachable = srv.post("/api/settings/test", json={"target": "tts"})
    assert unreachable.status_code == 200 and unreachable.json()["ok"] is False


def test_test_endpoint_validates_input(api, cfg_file):
    srv = api()
    assert srv.post("/api/settings/test", json={}).status_code == 400
    nope = srv.post("/api/settings/test", json={"target": "nope"})
    assert nope.status_code == 400 and "vidgrab" in err(nope)["hint"]
    bad = srv.post("/api/settings/test", json={"target": "tts", "values": {"tts_base_url": "x"}})
    assert bad.status_code == 400 and "tts_base_url" in err(bad)["fields"]
    assert srv.post("/api/settings/test", json={"target": "tts", "values": "x"}).status_code == 400
    assert srv.get("/api/settings/test").status_code == 404
