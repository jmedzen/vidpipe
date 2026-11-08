"""MCP server：協定、工具、端到端（真的開子行程做片）、stdio 與 HTTP 兩種傳輸。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from mediagen import color_clip
from test_server import Running
from ttsfake import FakeTTS

import vidpipe
from vidpipe import manifest as manifest_mod
from vidpipe import mcp, server

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

FPS = 10


@pytest.fixture
def env(tmp_path, fake_server, monkeypatch):
    FakeTTS(fake_server, speech=lambda text: 0.1 * len(text))
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PYTHONPATH", str(Path(vidpipe.__file__).parent.parent))
    return tmp_path


@pytest.fixture
def clip(env):
    return color_clip(env / "red.mp4", ["red"], seg=4, fps=FPS)


def doc(clip, **top):
    return {"schema": "vidpipe.manifest/1", "id": "m1", "title": "測試", "output": {"fps": FPS},
            "subtitles": {"burn_in": False},
            "scenes": [{"id": "a", "text": "你好呀。", "clip": {"path": str(clip)}}], **top}


def call(name, **arguments):
    """in-process 呼叫一個工具，回 (isError, structuredContent)。"""
    res = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})["result"]
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]
    return res["isError"], res["structuredContent"]


def rpc(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# ------------------------------------------------------------------ 協定

def test_initialize_negotiates_the_protocol_version_and_describes_the_server():
    reply = mcp.handle_message(rpc("initialize", {"protocolVersion": "2025-03-26"}))
    result = reply["result"]
    assert reply["id"] == 1 and result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "vidpipe" and "make_short" in result["instructions"]
    unknown = mcp.handle_message(rpc("initialize", {"protocolVersion": "1999-01-01"}))
    assert unknown["result"]["protocolVersion"] == mcp.SUPPORTED_PROTOCOLS[0]
    assert mcp.handle_message(rpc("initialize"))["result"]["protocolVersion"] \
        == mcp.SUPPORTED_PROTOCOLS[0]


def test_tools_list_matches_the_handlers_and_every_tool_has_a_schema():
    tools = mcp.handle_message(rpc("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == list(mcp.HANDLERS)
    for tool in tools:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"
        assert set(tool["inputSchema"]["required"]) <= set(tool["inputSchema"]["properties"])
    json.dumps(tools)       # 整份可以序列化
    step = next(t for t in tools if t["name"] == "run_step")
    assert step["inputSchema"]["properties"]["op"]["enum"] == [
        "narrate", "render", "cover", "credits"]


def test_ping_notifications_and_malformed_messages():
    assert mcp.handle_message(rpc("ping", msg_id="p")) == {"jsonrpc": "2.0", "id": "p",
                                                           "result": {}}
    assert mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    unknown = mcp.handle_message(rpc("resources/list"))
    assert unknown["error"]["code"] == -32601 and unknown["id"] == 1
    # 不認得的通知（沒有 id）不回應
    assert mcp.handle_message({"jsonrpc": "2.0", "method": "whatever"}) is None
    for bad in ("x", 3, None, {"method": "ping"}, {"jsonrpc": "1.0", "method": "ping", "id": 1}):
        assert mcp.handle_message(bad)["error"]["code"] == -32600
    assert mcp.handle_message(rpc("ping", params=[1]))["error"]["code"] == -32602
    assert mcp.handle_message(rpc("tools/call", {}))["error"]["code"] == -32602
    assert mcp.handle_message(
        rpc("tools/call", {"name": "list_shorts", "arguments": [1]}))["error"]["code"] == -32602


def test_batches_reply_in_order_and_drop_notifications():
    batch = [rpc("ping", msg_id=1), {"jsonrpc": "2.0", "method": "notifications/initialized"},
             rpc("tools/list", msg_id=2)]
    replies = mcp.handle_payload(batch)
    assert [r["id"] for r in replies] == [1, 2]
    assert mcp.handle_payload([{"jsonrpc": "2.0", "method": "notifications/initialized"}]) is None
    assert mcp.handle_payload([])["error"]["code"] == -32600


# ------------------------------------------------------------------ 工具（不需要 ffmpeg）

def test_unknown_tool_and_bad_arguments_are_tool_errors_not_protocol_errors(env):
    is_error, body = call("nope")
    assert is_error and body["error"]["code"] == "not_found" and body["ok"] is False
    is_error, body = call("get_job")
    assert is_error and body["error"]["code"] == "bad_input"
    is_error, body = call("get_job", id="zzz")
    assert is_error and body["error"]["code"] == "bad_input"          # 不是合法的 id
    is_error, body = call("get_job", id="20250101-000000-abcdef")
    assert is_error and body["error"]["code"] == "not_found"
    is_error, body = call("run_step", op="explode", manifest={})
    assert is_error and "op 不認得" in body["error"]["message"]
    is_error, body = call("make_short")
    assert is_error and "manifest" in body["error"]["message"]
    is_error, body = call("make_short", manifest={}, wait=-1)
    assert is_error and "wait" in body["error"]["message"]
    is_error, body = call("run_step", op="cover", manifest={}, at="1")
    assert is_error and "at" in body["error"]["message"]
    is_error, body = call("get_short", id="nope")
    assert is_error and body["error"]["code"] == "not_found"
    is_error, body = call("cancel_job", id="20250101-000000-abcdef")
    assert is_error and body["error"]["code"] == "not_found"


def test_manifest_guide_documents_every_top_level_field_and_the_schema():
    is_error, body = call("manifest_guide")
    assert not is_error and body["schema"] == manifest_mod.SCHEMA
    for key in ("schema", "id", "title", "output", "voice", "timing", "scenes", "subtitles",
                "music", "cover"):
        assert f'"{key}"' in body["guide"], key
    assert "validate_manifest" in body["guide"] and "asset_id" in body["guide"]


def test_validate_manifest_describes_scenes_and_reports_the_path_of_a_bad_field(env, clip):
    is_error, body = call("validate_manifest", manifest=doc(clip))
    assert not is_error and body["ok"] and body["short_id"] == "m1"
    assert body["scenes"][0]["sentences"] == ["你好呀。"]
    bad = doc(clip)
    bad["scenes"][0]["txt"] = "拼錯了"
    is_error, body = call("validate_manifest", manifest=bad)
    assert is_error and body["error"]["code"] == "bad_input"
    assert "scenes[0]" in body["error"]["message"] and "txt" in body["error"]["message"]
    assert not (env / "work" / "shorts").exists()       # 只驗證，不留任何東西


def test_list_shorts_is_empty_at_first_and_check_setup_is_a_report_not_an_error(
        env, monkeypatch):
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", "http://127.0.0.1:9")       # 沒人聽的 port
    assert call("list_shorts") == (False, {"ok": True, "shorts": []})
    # vidgrab 沒在跑：回報 ok=False，但這是狀態報告，不是呼叫失敗
    is_error, body = call("check_setup", skip=["cjk_font", "ffmpeg_subtitles"])
    assert not is_error and body["skipped"] == ["cjk_font", "ffmpeg_subtitles"]
    assert {c["name"] for c in body["checks"]} >= {"ffmpeg", "vidgrab", "tts"}
    assert body["ok"] is False                              # vidgrab 連不上，必要項目沒過
    assert next(c for c in body["checks"] if c["name"] == "vidgrab")["ok"] is False
    is_error, body = call("check_setup", skip="vidgrab")
    assert is_error and body["error"]["code"] == "bad_input"
    is_error, body = call("check_setup", skip=["nope"])
    assert is_error and "nope" in body["error"]["message"]


# ------------------------------------------------------------------ 端到端

@needs_ffmpeg
def test_make_short_runs_the_whole_pipeline_and_returns_the_files(env, clip):
    is_error, body = call("make_short", manifest=doc(clip), wait=120)
    assert not is_error, body
    assert body["ok"] and body["done"] and body["job"]["status"] == "succeeded"
    assert body["job"]["kind"] == "make" and body["job"]["result"]["short_id"] == "m1"
    assert Path(body["job"]["result"]["path"]).is_file()
    short = body["short"]
    assert short["stage"] == "rendered" and short["total"] > 0
    names = {f["name"]: f["url"] for f in short["files"]}
    assert names["m1.mp4"] == "/file/m1/out/m1.mp4"
    assert {"m1.cover.jpg", "m1.credits.md"} <= set(names)

    is_error, detail = call("get_short", id="m1")
    assert not is_error and detail["short"]["manifest"]["id"] == "m1"
    assert detail["short"]["sources"]["short_id"] == "m1"
    assert detail["short"]["jobs"][0]["id"] == body["job"]["id"]
    assert call("list_shorts")[1]["shorts"][0]["id"] == "m1"
    again = call("get_job", id=body["job"]["id"])
    assert again[0] is False and again[1]["done"] and again[1]["short"]["id"] == "m1"


@needs_ffmpeg
def test_run_step_cover_redoes_only_the_cover_after_a_make(env, clip):
    call("make_short", manifest=doc(clip), wait=120)
    first = (env / "out" / "m1.cover.jpg").read_bytes()
    is_error, body = call("run_step", op="cover", manifest=doc(clip), at=0.5, text="新標題",
                          wait=60)
    assert not is_error and body["job"]["kind"] == "cover", body
    assert body["job"]["result"]["text"] == "新標題" and body["job"]["result"]["at"] == 0.5
    assert (env / "out" / "m1.cover.jpg").read_bytes() != first


@needs_ffmpeg
def test_a_failing_job_comes_back_as_a_tool_error_with_its_reason(env, clip):
    # 沒 narrate 過就 render：工作會失敗，錯誤帶 hint 告訴 agent 先做什麼
    is_error, body = call("run_step", op="render", manifest=doc(clip), wait=60)
    assert is_error and body["ok"] is False and body["done"] is True
    assert body["job"]["status"] == "failed"
    assert body["error"]["code"] == "not_found" and "narrate" in body["error"]["hint"]


@needs_ffmpeg
def test_a_job_that_outlasts_wait_returns_a_job_id_that_get_job_can_wait_on_and_cancel(
        env, clip, fake_server):
    FakeTTS(fake_server, speech=lambda text: 0.5, delay=30.0)       # 合成要 30 秒
    is_error, body = call("make_short", manifest=doc(clip), wait=0)
    assert not is_error and body["ok"] and body["done"] is False
    job_id = body["job"]["id"]
    assert job_id in body["hint"] and "get_job" in body["hint"]
    assert "short" not in body

    is_error, busy = call("make_short", manifest=doc(clip), wait=0)       # 同一支片只能一個工作
    assert is_error and busy["error"]["code"] == "conflict" and job_id in busy["error"]["message"]

    started = time.monotonic()
    is_error, waiting = call("get_job", id=job_id, wait=1)
    assert not is_error and waiting["done"] is False
    assert 0.9 <= time.monotonic() - started < 10

    is_error, cancelled = call("cancel_job", id=job_id)
    assert not is_error and cancelled["signalled"] is True
    is_error, final = call("get_job", id=job_id, wait=30)
    assert final["done"] is True and final["job"]["status"] == "cancelled"
    assert is_error is True and final["ok"] is False        # 取消的工作對呼叫端來說是沒做成


# ------------------------------------------------------------------ stdio

def run_stdio(env, lines, *, timeout=60):
    proc = subprocess.run(
        [sys.executable, "-m", "vidpipe", "mcp"], input="\n".join(lines) + "\n", text=True,
        capture_output=True, timeout=timeout, cwd=env, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return proc, [json.loads(line) for line in proc.stdout.splitlines()]


def test_stdio_speaks_one_json_rpc_message_per_line_and_nothing_else_on_stdout(env):
    lines = [
        json.dumps(rpc("initialize", {"protocolVersion": "2025-06-18"}, msg_id=1)),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "",
        "{not json",
        json.dumps(rpc("tools/list", msg_id=2)),
        json.dumps(rpc("tools/call", {"name": "list_shorts", "arguments": {}}, msg_id=3)),
        json.dumps(rpc("ping", msg_id=4)),
    ]
    proc, replies = run_stdio(env, lines)
    by_id = {r["id"]: r for r in replies}
    assert set(by_id) == {1, 2, 3, 4, None}
    assert by_id[None]["error"]["code"] == -32700
    assert by_id[1]["result"]["serverInfo"]["name"] == "vidpipe"
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == list(mcp.HANDLERS)
    assert by_id[3]["result"]["structuredContent"] == {"ok": True, "shorts": []}
    assert by_id[4]["result"] == {}


@needs_ffmpeg
def test_stdio_can_cancel_a_job_while_make_short_is_still_waiting(env, clip, fake_server):
    FakeTTS(fake_server, speech=lambda text: 0.5, delay=30.0)
    proc = subprocess.Popen([sys.executable, "-m", "vidpipe", "mcp"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", cwd=env)

    def send(msg):
        proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def read():
        return json.loads(proc.stdout.readline())

    try:
        send(rpc("tools/call", {"name": "make_short",
                                "arguments": {"manifest": doc(clip), "wait": 120}}, msg_id=1))
        job_id = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and job_id is None:
            send(rpc("tools/call", {"name": "list_shorts", "arguments": {}}, msg_id=2))
            # 第一個呼叫還卡著，這個照回
            shorts = read()["result"]["structuredContent"]["shorts"]
            job_id = shorts[0]["last_job"]["id"] if shorts and shorts[0]["last_job"] else None
            time.sleep(0.2)
        assert job_id, "工作沒有啟動"
        send(rpc("tools/call", {"name": "cancel_job", "arguments": {"id": job_id}}, msg_id=3))
        replies = {}
        while 1 not in replies:
            reply = read()
            replies[reply["id"]] = reply
        final = replies[1]["result"]["structuredContent"]
        assert final["job"]["status"] == "cancelled" and final["done"] is True
        assert replies[1]["result"]["isError"] is True
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
        proc.stdout.close()
        proc.stderr.close()


# ------------------------------------------------------------------ HTTP

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


def test_http_mcp_answers_requests_acknowledges_notifications_and_rejects_get(api, env):
    run = api()
    resp = run.post("/mcp", json=rpc("tools/list"))
    assert resp.status_code == 200 and resp.headers["Content-Type"].startswith("application/json")
    assert [t["name"] for t in resp.json()["result"]["tools"]] == list(mcp.HANDLERS)

    note = run.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and note.content == b""

    batch = run.post("/mcp", json=[rpc("ping", msg_id=1), rpc("ping", msg_id=2)])
    assert [r["id"] for r in batch.json()] == [1, 2]

    called = run.post("/mcp", json=rpc("tools/call", {"name": "list_shorts"})).json()
    assert called["result"]["structuredContent"] == {"ok": True, "shorts": []}

    bad = run.post("/mcp", data=b"{nope", headers={"Content-Type": "application/json"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
    got = run.get("/mcp")
    assert got.status_code == 405 and got.headers["Allow"] == "POST"
    assert got.json()["error"]["code"] == "method_not_allowed"
    assert requests.delete(run.base + "/mcp", timeout=10).status_code == 405
    huge = run.post("/mcp", data=b"x" * (server.MAX_BODY + 1))
    assert huge.status_code == 400


def test_http_mcp_needs_the_token_and_the_same_origin_rules_as_the_api(api, env):
    run = api(token="s3cret")
    msg = rpc("tools/list")
    assert run.post("/mcp", json=msg).status_code == 401
    assert run.post("/mcp", json=msg, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert run.get("/mcp").status_code == 401
    ok = run.post("/mcp", json=msg, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200 and ok.json()["result"]["tools"]
    cross = run.post("/mcp", json=msg, headers={"Authorization": "Bearer s3cret",
                                                "Origin": "http://evil.example"})
    assert cross.status_code == 403
    rebind = run.post("/mcp", json=msg, headers={"Authorization": "Bearer s3cret",
                                                 "Host": "evil.example"})
    assert rebind.status_code == 403


@needs_ffmpeg
def test_http_mcp_makes_a_short_end_to_end_and_the_returned_url_serves_the_video(
        api, env, clip):
    run = api()
    reply = run.post("/mcp", json=rpc("tools/call", {
        "name": "make_short", "arguments": {"manifest": doc(clip), "wait": 120}})).json()
    result = reply["result"]
    assert result["isError"] is False, result
    video = next(f for f in result["structuredContent"]["short"]["files"]
                 if f["name"] == "m1.mp4")
    got = run.get(video["url"])
    assert got.status_code == 200 and len(got.content) == video["size_bytes"]
