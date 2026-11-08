"""作品管理：plan 之後（planned）的狀態、manifest 的來源、刪除。檔案手動放，不需要 ffmpeg。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_server import Running

from vidpipe import config as config_mod
from vidpipe import jobs, server, shorts
from vidpipe.errors import NotFound


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    return config_mod.load()


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


def put(path: Path, content, *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False) if not isinstance(content, str)
                    else content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def work(cfg, short_id: str) -> Path:
    return Path(cfg.work_dir) / "shorts" / short_id


def plan_doc(title="夜市", scenes=2):
    return {"schema": "vidpipe.plan/1", "report": {"short_id": "t1", "scenes": [], "warnings": []},
            "manifest": {"id": "t1", "title": title, "scenes": [{"id": f"s{i}"}
                                                                 for i in range(scenes)]}}


# ------------------------------------------------------------------ 狀態與 manifest 的來源

def test_a_short_with_only_a_plan_is_planned_and_takes_its_title_and_scenes_from_it(cfg):
    put(work(cfg, "t1") / "plan.json", plan_doc())
    summary = shorts.summarize(cfg, "t1")
    assert (summary["stage"], summary["title"], summary["scenes"]) == ("planned", "夜市", 2)
    detail = shorts.detail(cfg, "t1")
    assert detail["manifest_source"] == "plan" and detail["manifest"]["title"] == "夜市"
    assert detail["plan"] == {"short_id": "t1", "scenes": [], "warnings": []}


def test_manifest_json_wins_unless_a_newer_plan_replaces_it(cfg):
    root = work(cfg, "t1")
    put(root / "plan.json", plan_doc("計畫版", 3), mtime=1000)
    put(root / "manifest.json", {"id": "t1", "title": "送去做的版本", "scenes": [{}]}, mtime=2000)
    assert shorts.detail(cfg, "t1")["manifest_source"] == "manifest"
    assert shorts.summarize(cfg, "t1")["title"] == "送去做的版本"
    os.utime(root / "plan.json", (3000, 3000))              # 重新 plan 過：以新的挑選為準
    detail = shorts.detail(cfg, "t1")
    assert detail["manifest_source"] == "plan" and detail["manifest"]["title"] == "計畫版"
    assert shorts.summarize(cfg, "t1")["scenes"] == 3


def test_stage_follows_the_furthest_step_not_the_plan(cfg):
    root = work(cfg, "t1")
    put(root / "plan.json", plan_doc())
    put(root / "timeline.json", {"total": 3.0, "scenes": []})
    assert shorts.summarize(cfg, "t1")["stage"] == "narrated"
    put(Path(cfg.out_dir) / "t1.mp4", "x")
    assert shorts.summarize(cfg, "t1")["stage"] == "rendered"


def test_a_draft_alone_gives_the_list_a_title_and_scene_count_but_no_manifest_to_make(cfg):
    put(work(cfg, "t1") / "plan-request.json",
        {"id": "t1", "title": "草稿", "scenes": [{"text": "好", "query": ["a"]}]})
    summary = shorts.summarize(cfg, "t1")
    assert (summary["stage"], summary["title"], summary["scenes"]) == ("new", "草稿", 1)
    detail = shorts.detail(cfg, "t1")
    assert detail["manifest_source"] == "draft" and detail["plan"] is None


def test_nothing_readable_leaves_the_manifest_empty(cfg):
    put(work(cfg, "t1") / "manifest.json", "{not json")
    detail = shorts.detail(cfg, "t1")
    assert detail["manifest"] is None and detail["manifest_source"] is None
    assert detail["title"] == "" and detail["scenes"] == 0


# ------------------------------------------------------------------ 刪除

def seed(cfg, short_id: str) -> None:
    put(work(cfg, short_id) / "manifest.json", {"id": short_id, "scenes": []})
    put(work(cfg, short_id) / "render" / "seg-0.mp4", "x")
    for suffix in (".mp4", ".cover.jpg", ".credits.md", ".srt"):
        put(Path(cfg.out_dir) / f"{short_id}{suffix}", "x")


def test_delete_removes_the_work_folder_and_the_outputs_but_not_other_shorts_or_the_cache(
        cfg, api):
    seed(cfg, "gone")
    seed(cfg, "keep")
    put(Path(cfg.work_dir) / "cache" / "tts" / "a.wav", "x")
    run = api()
    resp = run.delete("/api/short/gone")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True and body["short_id"] == "gone"
    assert set(body["removed"]) == {"out/gone.mp4", "out/gone.cover.jpg", "out/gone.credits.md",
                                    "out/gone.srt", "work/manifest.json"}
    assert not work(cfg, "gone").exists()
    assert not list(Path(cfg.out_dir).glob("gone*"))
    assert (work(cfg, "keep") / "render" / "seg-0.mp4").is_file()
    assert (Path(cfg.out_dir) / "keep.mp4").is_file()
    assert (Path(cfg.work_dir) / "cache" / "tts" / "a.wav").is_file()
    assert [s["id"] for s in run.get("/api/shorts").json()["shorts"]] == ["keep"]
    assert run.get("/api/short/gone").status_code == 404


def test_delete_an_unknown_short_or_a_bad_id_is_refused_without_touching_anything(cfg, api):
    seed(cfg, "keep")
    run = api()
    missing = run.delete("/api/short/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    bad = run.delete("/api/short/..%2F..%2Fout")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_input"
    assert run.delete("/api/short/Keep").status_code == 400
    assert (Path(cfg.out_dir) / "keep.mp4").is_file()
    with pytest.raises(NotFound):
        shorts.delete(cfg, "nope")


def test_a_short_with_a_running_job_cannot_be_deleted(cfg, api):
    seed(cfg, "busy")
    run = api()
    with jobs.JobRecorder(cfg.work_dir, "make", short_id="busy") as job:
        resp = run.delete("/api/short/busy")
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "conflict"
        assert job.id in resp.json()["error"]["message"]
        assert (work(cfg, "busy") / "manifest.json").is_file()
    assert run.delete("/api/short/busy").status_code == 200       # 工作結束後就可以


def test_delete_goes_through_the_token_and_origin_guards_like_every_write(cfg, api):
    seed(cfg, "keep")
    run = api(token="s3cret")
    assert run.delete("/api/short/keep").status_code == 401
    cross = run.delete("/api/short/keep", headers={"Authorization": "Bearer s3cret",
                                                   "Origin": "http://evil.example"})
    assert cross.status_code == 403
    assert (Path(cfg.out_dir) / "keep.mp4").is_file()
    ok = run.delete("/api/short/keep", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
