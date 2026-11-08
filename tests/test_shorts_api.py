"""HTTP API：送件（驗證、開子行程跑工作）、查狀態、取成品；`make` 一次做完。"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from mediagen import color_clip
from test_server import Running
from ttsfake import FakeTTS

import vidpipe
from vidpipe import cli, jobs, server

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

FPS = 10


@pytest.fixture
def env(tmp_path, fake_server, monkeypatch):
    FakeTTS(fake_server, speech=lambda text: 0.1 * len(text))
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    # 子行程的工作目錄是 tmp_path；它得找得到這份 vidpipe（本機用 PYTHONPATH=src 跑時是相對路徑）
    monkeypatch.setenv("PYTHONPATH", str(Path(vidpipe.__file__).parent.parent))
    return tmp_path


@pytest.fixture
def clip(env):
    return color_clip(env / "red.mp4", ["red"], seg=4, fps=FPS)


def doc(clip, **top):
    return {"schema": "vidpipe.manifest/1", "id": "r1", "title": "測試", "output": {"fps": FPS},
            "subtitles": {"burn_in": False},
            "scenes": [{"id": "a", "text": "你好呀。", "clip": {"path": str(clip)}}], **top}


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


def wait_job(run: Running, job_id: str, *, timeout: float = 120.0, headers=None) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = run.get(f"/api/job/{job_id}", headers=headers).json()["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError(f"工作 {job_id} 逾時")


# ------------------------------------------------------------------ validate

def test_validate_describes_a_manifest_and_reports_the_path_of_a_bad_field(api, clip):
    run = api()
    ok = run.post("/api/validate", json={"manifest": doc(clip)})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] and body["short_id"] == "r1"
    assert body["scenes"][0]["sentences"] == ["你好呀。"]

    bad = doc(clip)
    bad["scenes"][0]["txt"] = "typo"
    resp = run.post("/api/validate", json={"manifest": bad})
    assert resp.status_code == 400 and "scenes[0]" in resp.json()["error"]["message"]
    assert run.post("/api/validate", json={}).json()["error"]["code"] == "bad_input"
    assert run.post("/api/validate", data="not json").status_code == 400
    assert run.post("/api/validate", json=[1]).status_code == 400


def test_a_rejected_manifest_never_starts_a_job_or_leaves_a_folder(api, clip, env):
    run = api()
    bad = doc(clip)
    bad["scenes"][0]["txt"] = "typo"
    resp = run.post("/api/render", json={"manifest": bad})
    assert resp.status_code == 400
    assert run.get("/api/jobs").json()["jobs"] == []
    assert not (env / "work" / "shorts").exists()
    assert run.post("/api/nope", json={}).status_code == 404


# ------------------------------------------------------------------ 一次做完

def test_make_runs_as_a_background_job_and_the_finished_short_can_be_downloaded(api, clip, env):
    run = api()
    resp = run.post("/api/make", json={"manifest": doc(clip)})
    assert resp.status_code == 202
    started = resp.json()
    assert started["op"] == "make" and started["short_id"] == "r1"
    assert started["status_url"] == f"/api/job/{started['job']}"

    job = wait_job(run, started["job"])
    assert job["status"] == "succeeded", job
    assert job["kind"] == "make" and job["short_id"] == "r1"
    assert [s["name"] for s in job["stages"]] == [
        "narrate", "timeline", "assets", "segments", "audio", "mux"]

    detail = run.get("/api/short/r1").json()["short"]
    assert detail["stage"] == "rendered" and detail["title"] == "測試"
    assert detail["timeline"]["total"] > 0 and detail["sources"]["short_id"] == "r1"
    assert detail["manifest"]["id"] == "r1"
    assert detail["jobs"][0]["id"] == started["job"]
    names = {f["name"] for f in detail["files"]}
    assert {"r1.mp4", "r1.cover.jpg", "r1.credits.md", "timeline.json", "narration.wav",
            "sources.json", "manifest.json"} <= names

    listing = run.get("/api/shorts").json()["shorts"]
    assert [s["id"] for s in listing] == ["r1"]
    assert listing[0]["stage"] == "rendered" and listing[0]["last_job"]["status"] == "succeeded"

    video = next(f for f in detail["files"] if f["name"] == "r1.mp4")
    full = run.get(video["url"])
    assert full.status_code == 200 and full.headers["Content-Type"] == "video/mp4"
    assert len(full.content) == video["size_bytes"] == (env / "out" / "r1.mp4").stat().st_size
    assert full.headers["Accept-Ranges"] == "bytes"
    assert run.get("/file/r1/out/r1.credits.md").headers["Content-Type"].startswith(
        "text/markdown")
    assert run.get("/file/r1/work/timeline.json").json()["fps"] == FPS


def test_range_requests_serve_the_video_in_pieces(api, clip, env):
    run = api()
    wait_job(run, run.post("/api/make", json={"manifest": doc(clip)}).json()["job"])
    data = (env / "out" / "r1.mp4").read_bytes()
    url = "/file/r1/out/r1.mp4"
    part = run.get(url, headers={"Range": "bytes=10-99"})
    assert part.status_code == 206 and part.content == data[10:100]
    assert part.headers["Content-Range"] == f"bytes 10-99/{len(data)}"
    assert run.get(url, headers={"Range": "bytes=-50"}).content == data[-50:]
    assert run.get(url, headers={"Range": f"bytes={len(data) - 5}-"}).content == data[-5:]
    over = run.get(url, headers={"Range": f"bytes={len(data) + 10}-"})
    assert over.status_code == 416 and over.headers["Content-Range"] == f"bytes */{len(data)}"
    assert run.get(url, headers={"Range": "bytes=5-2"}).status_code == 416
    assert run.get(url, headers={"Range": "items=0-1"}).status_code == 416


def test_only_known_files_are_served_and_ids_cannot_escape(api, clip, env):
    run = api()
    wait_job(run, run.post("/api/make", json={"manifest": doc(clip)}).json()["job"])
    (env / "work" / "shorts" / "r1" / "notes.txt").write_text("private", encoding="utf-8")
    (env / "out" / "other.mp4").write_bytes(b"x")
    for path in ("/file/r1/work/notes.txt", "/file/r1/out/other.mp4", "/file/r1/out/r1.exe",
                 "/file/r1/nowhere/manifest.json", "/file/r1/work", "/file/r1"):
        assert run.get(path).status_code == 404, path
    assert run.get("/file/r1/work/..%2F..%2Fsecret.json").status_code == 404
    assert run.get("/file/r1/out/..%2Fwork%2Fshorts%2Fr1%2Fnotes.txt").status_code == 404
    assert run.get("/file/..%2Fx/work/manifest.json").status_code == 400
    assert run.get("/api/short/nope").status_code == 404
    assert run.get("/api/short/..%2Fx").status_code == 400


def test_the_token_protects_the_new_endpoints_too(api, clip):
    run = api(token="s3cret")
    auth = {"Authorization": "Bearer s3cret"}
    for method, path in (("get", "/api/shorts"), ("get", "/api/short/r1"),
                         ("get", "/file/r1/out/r1.mp4"), ("post", "/api/make"),
                         ("post", "/api/validate")):
        assert getattr(run, method)(path).status_code == 401, path
    assert run.post("/api/validate", json={"manifest": doc(clip)}, headers=auth).status_code == 200
    started = run.post("/api/make", json={"manifest": doc(clip)}, headers=auth)
    assert started.status_code == 202
    assert wait_job(run, started.json()["job"], headers=auth)["status"] == "succeeded"


# ------------------------------------------------------------------ 單步與衝突、取消

def test_cover_and_credits_jobs_work_on_the_last_render(api, clip, env):
    run = api()
    wait_job(run, run.post("/api/make", json={"manifest": doc(clip)}).json()["job"])
    started = run.post("/api/cover", json={"manifest": doc(clip), "at": 0.5, "text": ""})
    assert started.status_code == 202
    job = wait_job(run, started.json()["job"])
    assert job["status"] == "succeeded" and job["kind"] == "cover"
    assert job["result"]["at"] == 0.5 and job["result"]["text"] is None
    credits = wait_job(run, run.post("/api/credits", json={"manifest": doc(clip)}).json()["job"])
    assert credits["status"] == "succeeded" and credits["kind"] == "credits"
    assert run.post("/api/cover", json={"manifest": doc(clip), "at": "x"}).status_code == 400
    assert run.post("/api/cover", json={"manifest": doc(clip), "text": 3}).status_code == 400


def test_a_failing_job_is_recorded_as_failed_with_its_structured_error(api, clip, env):
    run = api()
    resp = run.post("/api/render", json={"manifest": doc(clip)})    # 沒 narrate 過
    job = wait_job(run, resp.json()["job"])
    assert job["status"] == "failed" and job["error"]["code"] == "not_found"
    assert "narrate" in job["error"]["hint"]


def test_one_running_job_per_short_and_a_running_job_can_be_cancelled(
        api, clip, env, fake_server):
    FakeTTS(fake_server, speech=lambda text: 0.5, delay=30.0)    # 合成要 30 秒：工作會停在那
    run = api()
    first = run.post("/api/narrate", json={"manifest": doc(clip)})
    assert first.status_code == 202
    again = run.post("/api/render", json={"manifest": doc(clip)})
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"
    assert first.json()["job"] in again.json()["error"]["message"]
    other = doc(clip, id="r2")
    assert run.post("/api/validate", json={"manifest": other}).status_code == 200

    cancel = run.post(f"/api/job/{first.json()['job']}/cancel")
    assert cancel.status_code == 200
    assert wait_job(run, first.json()["job"], timeout=30)["status"] == "cancelled"
    # 取消後可以再送（narrate 被取消了、沒有時間軸，所以這個工作會失敗，但它確實啟動了）
    retry = run.post("/api/credits", json={"manifest": doc(clip)})
    assert retry.status_code == 202
    failed = wait_job(run, retry.json()["job"])
    assert failed["status"] == "failed" and failed["error"]["code"] == "not_found"


# ------------------------------------------------------------------ CLI make／純函式

def test_cli_make_does_narrate_and_render_in_one_job(env, clip, capsys):
    path = env / "m.json"
    path.write_text(json.dumps(doc(clip), ensure_ascii=False), encoding="utf-8")
    assert cli.main(["make", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    result = out["output"]
    assert Path(result["path"]).is_file() and result["narrate"]["tts"]["sentences"] == 1
    assert result["cover"]["path"].endswith("r1.cover.jpg")
    state = jobs.get_job(env / "work", out["job"])
    assert state["kind"] == "make" and state["status"] == "succeeded"
    assert [s["name"] for s in state["stages"]] == [
        "narrate", "timeline", "assets", "segments", "audio", "mux"]
    assert set(state["result"]) == {"short_id", "path", "duration", "size_bytes", "warnings"}


def test_parse_range_handles_the_single_range_forms():
    parse = server._parse_range
    assert parse("bytes=0-9", 100) == (0, 9)
    assert parse("bytes=90-", 100) == (90, 99)
    assert parse("bytes=-10", 100) == (90, 99)
    assert parse("bytes=50-5000", 100) == (50, 99)
    assert parse("bytes=-500", 100) == (0, 99)
    for bad in ("bytes=100-", "bytes=9-3", "bytes=-", "bytes=-0", "bytes=1-2,5-6", "x", ""):
        assert parse(bad, 100) is None, bad
    assert parse("bytes=0-0", 0) is None
