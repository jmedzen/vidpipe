"""作品管理：plan 之後（planned）的狀態、manifest 的來源、刪除、網頁編輯寫回。
檔案手動放，不需要 ffmpeg。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_server import Running

from vidpipe import config as config_mod
from vidpipe import jobs, server, shorts, timeline
from vidpipe.errors import BadInput, NotFound


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


# ------------------------------------------------------------------ 編輯寫回

def doc_of(short_id="t1", text="夜市很熱鬧。", **scene):
    return {"schema": "vidpipe.manifest/1", "id": short_id,
            "scenes": [{"id": "s1", "text": text, **scene}]}


def seed_edit(cfg, short_id="t1", **kw) -> str:
    """一支有 manifest.json 的片；回它現在的 revision。"""
    doc = doc_of(short_id, **kw)
    put(work(cfg, short_id) / "manifest.json", doc)
    return shorts.revision_of(doc)


def narrated(cfg, doc, short_id="t1", *, rendered=True) -> None:
    """放一份「照這份 manifest 做出來」的 timeline（與 sources）。"""
    manifest, effective = shorts.parse_document(doc, cfg)
    fp = timeline.fingerprint(manifest, doc, effective)
    put(work(cfg, short_id) / "timeline.json", {"total": 3.0, "scenes": [], "fingerprint": fp})
    if rendered:
        put(work(cfg, short_id) / "sources.json", {"fingerprint": fp, "scenes": []})


def test_the_revision_is_the_same_for_the_same_content_whatever_the_key_order(cfg):
    a = {"id": "t1", "scenes": [{"id": "s1", "text": "好"}]}
    b = {"scenes": [{"text": "好", "id": "s1"}], "id": "t1"}
    assert shorts.revision_of(a) == shorts.revision_of(b) is not None
    assert shorts.revision_of({**a, "title": "x"}) != shorts.revision_of(a)
    assert shorts.revision_of(None) is None and shorts.revision_of([1]) is None
    put(work(cfg, "t1") / "manifest.json", a)
    assert shorts.detail(cfg, "t1")["manifest_revision"] == shorts.revision_of(a)


def test_update_manifest_writes_the_new_document_and_returns_its_revision(cfg):
    revision = seed_edit(cfg)
    new = doc_of(text="夜市很熱鬧，人很多。", fit="crop")
    done = shorts.update_manifest(cfg, "t1", new, revision)
    assert done == {"short_id": "t1", "manifest_revision": shorts.revision_of(new)}
    saved = json.loads((work(cfg, "t1") / "manifest.json").read_text(encoding="utf-8"))
    assert saved == new
    detail = shorts.detail(cfg, "t1")
    assert detail["manifest"] == new and detail["manifest_revision"] == done["manifest_revision"]
    # 拿舊的 revision 再存一次就是衝突：已經有人（這次）改過了
    with pytest.raises(shorts.StaleManifest):
        shorts.update_manifest(cfg, "t1", doc_of(text="又改"), revision)


def test_a_plan_that_is_newer_than_the_manifest_is_what_gets_edited_and_then_wins(cfg):
    root = work(cfg, "t1")
    put(root / "manifest.json", doc_of(text="舊的"), mtime=1000)
    planned = {**doc_of(text="新 plan 的版本"), "title": "計畫版"}
    put(root / "plan.json", {"schema": "vidpipe.plan/1", "report": {"scenes": []},
                             "manifest": planned}, mtime=2000)
    shown = shorts.detail(cfg, "t1")
    assert shown["manifest_source"] == "plan" and shown["manifest"] == planned
    edited = {**planned, "title": "改過的"}
    shorts.update_manifest(cfg, "t1", edited, shown["manifest_revision"])
    after = shorts.detail(cfg, "t1")
    assert after["manifest_source"] == "manifest" and after["manifest"] == edited


def test_update_manifest_refuses_a_stale_revision_without_touching_the_file(cfg):
    seed_edit(cfg)
    before = (work(cfg, "t1") / "manifest.json").read_text(encoding="utf-8")
    for bad in ("0123456789abcdef", ""):
        with pytest.raises((shorts.StaleManifest, BadInput)):
            shorts.update_manifest(cfg, "t1", doc_of(text="蓋掉"), bad)
    for missing in (None, 5):
        with pytest.raises(BadInput, match="revision"):
            shorts.update_manifest(cfg, "t1", doc_of(text="蓋掉"), missing)
    assert (work(cfg, "t1") / "manifest.json").read_text(encoding="utf-8") == before


def test_update_manifest_validates_like_everything_else_and_keeps_the_id(cfg):
    revision = seed_edit(cfg)
    broken = doc_of(fitt="crop")                              # 打錯字的欄位
    with pytest.raises(BadInput, match=r"scenes\[0\]"):
        shorts.update_manifest(cfg, "t1", broken, revision)
    with pytest.raises(BadInput):
        shorts.update_manifest(cfg, "t1", "not a document", revision)
    with pytest.raises(BadInput, match="t2"):
        shorts.update_manifest(cfg, "t1", doc_of("t2"), revision)
    assert not work(cfg, "t2").exists()
    assert json.loads((work(cfg, "t1") / "manifest.json").read_text(encoding="utf-8")
                      ) == doc_of()


def test_update_manifest_needs_a_manifest_and_a_known_short(cfg):
    with pytest.raises(NotFound):
        shorts.update_manifest(cfg, "nope", doc_of("nope"), "x")
    with pytest.raises(BadInput):
        shorts.update_manifest(cfg, "../x", doc_of("x"), "x")
    put(work(cfg, "draft") / "plan-request.json",
        {"id": "draft", "title": "草稿", "scenes": [{"text": "好", "query": ["a"]}]})
    with pytest.raises(BadInput, match="manifest"):
        shorts.update_manifest(cfg, "draft", doc_of("draft"), "x")
    assert not (work(cfg, "draft") / "manifest.json").exists()


def test_a_short_with_a_running_job_cannot_be_edited(cfg):
    revision = seed_edit(cfg)
    with jobs.JobRecorder(cfg.work_dir, "make", short_id="t1"):
        with pytest.raises(jobs.Conflict) as running:
            shorts.update_manifest(cfg, "t1", doc_of(text="跑到一半改"), revision)
        assert not isinstance(running.value, shorts.StaleManifest)      # 不是「被別人改過」
    shorts.update_manifest(cfg, "t1", doc_of(text="跑完了再改"), revision)


def test_outdated_tells_whether_the_last_narration_and_render_match_the_manifest(cfg):
    doc = doc_of()
    put(work(cfg, "t1") / "manifest.json", doc)
    assert shorts.detail(cfg, "t1")["outdated"] is None       # 還沒做過：沒有「過期」可言
    narrated(cfg, doc, rendered=False)
    assert shorts.detail(cfg, "t1")["outdated"] is False      # 還沒有成片，沒有東西過期
    narrated(cfg, doc)
    assert shorts.detail(cfg, "t1")["outdated"] is False
    narrated(cfg, doc, rendered=False)             # 時間軸對得上 manifest
    put(work(cfg, "t1") / "sources.json", {"fingerprint": "from-an-older-timeline"})
    assert shorts.detail(cfg, "t1")["outdated"] is True       # 成片比時間軸舊
    narrated(cfg, doc)
    put(work(cfg, "t1") / "manifest.json", doc_of(text="改過了"))
    assert shorts.detail(cfg, "t1")["outdated"] is True
    put(work(cfg, "t1") / "manifest.json", {"schema": "vidpipe.manifest/1", "oops": 1})
    assert shorts.detail(cfg, "t1")["outdated"] is None       # 讀不懂就不下判斷


def test_outdated_notices_a_voice_or_subtitle_setting_change_too(cfg, monkeypatch):
    doc = doc_of()
    put(work(cfg, "t1") / "manifest.json", doc)
    narrated(cfg, doc)
    assert shorts.detail(cfg, "t1")["outdated"] is False
    monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", "Some Other Font")
    assert shorts.detail(config_mod.load(), "t1")["outdated"] is True


def test_the_manifest_put_saves_then_reports_outdated_and_stale_revisions_get_409(cfg, api):
    doc = doc_of()
    put(work(cfg, "t1") / "manifest.json", doc)
    narrated(cfg, doc)
    run = api()
    seen = run.get("/api/short/t1").json()["short"]
    assert seen["outdated"] is False and seen["manifest_revision"] == shorts.revision_of(doc)

    edited = doc_of(text="夜市很熱鬧，人很多。")
    ok = run.put("/api/short/t1/manifest", json={"manifest": edited,
                                                   "revision": seen["manifest_revision"]})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "short_id": "t1",
                         "manifest_revision": shorts.revision_of(edited)}
    after = run.get("/api/short/t1").json()["short"]
    assert after["manifest"] == edited and after["outdated"] is True

    theirs = {"manifest": doc_of(text="別人的版本"), "revision": seen["manifest_revision"]}
    stale = run.put("/api/short/t1/manifest", json=theirs)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_manifest"
    assert run.get("/api/short/t1").json()["short"]["manifest"] == edited

    bad = run.put("/api/short/t1/manifest", json={"manifest": doc_of(fitt="x"),
                                                    "revision": after["manifest_revision"]})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_input"
    nothing = run.put("/api/short/t1/manifest", json={"manifest": edited})
    assert nothing.status_code == 400
    assert run.put("/api/short/nope/manifest", json={"manifest": edited,
                                                       "revision": "x"}).status_code == 404


def test_the_manifest_put_goes_through_the_token_and_origin_guards(cfg, api):
    revision = seed_edit(cfg)
    edited = doc_of(text="改過的")
    body = {"manifest": edited, "revision": revision}
    run = api(token="s3cret")
    assert run.put("/api/short/t1/manifest", json=body).status_code == 401
    cross = run.put("/api/short/t1/manifest", json=body,
                    headers={"Authorization": "Bearer s3cret", "Origin": "http://evil.example"})
    assert cross.status_code == 403
    assert json.loads((work(cfg, "t1") / "manifest.json").read_text(encoding="utf-8")
                      ) == doc_of()
    ok = run.put("/api/short/t1/manifest", json=body, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
