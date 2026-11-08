"""plan：草稿 → 搜尋、挑素材與音樂 → manifest。選片規則用純函式測，流程用假 vidgrab 測。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from vgfake import FakeVidgrab

import vidpipe
from vidpipe import cli, jobs, mcp, planner, server
from vidpipe import manifest as manifest_mod
from vidpipe.clients import VidgrabClient
from vidpipe.config import Config
from vidpipe.errors import BadInput, UpstreamError

CFG = Config()
S = planner.Settings()
cand = FakeVidgrab.candidate


# ------------------------------------------------------------------ 選片規則

def pick(cands, need=5.0, settings=S, used=frozenset(), **kw):
    best, alternatives, notes = planner.choose(cands, need, settings, set(used), **kw)
    return (best or {}).get("id"), [a["asset_id"] for a in alternatives], notes


def test_license_comes_first_even_when_a_worse_license_is_a_better_fit():
    best, rest, _ = pick([cand("a:1", kind="free-attribution", height=1920),
                          cand("a:2", kind="free", width=1920, height=1080)])
    assert best == "a:2" and rest == ["a:1"]       # 授權明確（free）勝過直式但要標註
    # 授權比長度重要：授權較好但太短的（會循環）仍勝過授權較差但夠長的
    best, rest, notes = pick([cand("a:1", kind="free", duration=2),
                              cand("a:2", kind="free-attribution", duration=30)], need=5)
    assert best == "a:1" and rest == ["a:2"] and "循環補長" in notes[0]


def test_unclear_licenses_are_left_out_unless_allowed_and_then_flagged():
    cands = [cand("a:1", kind="unknown"), cand("a:2", kind="check")]
    best, rest, notes = pick(cands)
    assert best is None and rest == [] and notes == []
    assert planner.skipped_count(cands, S) == 2
    allowed = planner.Settings(allow_unknown_license=True)
    best, rest, notes = pick(cands, settings=allowed)
    assert best == "a:2" and rest == ["a:1"]           # check 比 unknown 好
    assert any("公開發佈前要自己確認" in n for n in notes)


def test_a_clip_long_enough_beats_one_that_would_loop_and_the_note_says_when_none_is():
    best, _, notes = pick([cand("a:1", duration=3, height=1920),
                           cand("a:2", duration=9, width=1920, height=1080)], need=5)
    assert best == "a:2" and not any("循環" in n for n in notes)
    best, _, notes = pick([cand("a:1", duration=3)], need=5)
    assert best == "a:1" and "只有 3.0 秒" in notes[0] and "循環補長" in notes[0]
    # 剛好差在 MARGIN 以內不算短
    assert pick([cand("a:1", duration=5.5)], need=5)[2] == []


def test_orientation_ranks_before_resolution_and_landscape_goes_last_with_a_note():
    cands = [cand("land", width=1920, height=1080), cand("sq", width=1000, height=1000),
             cand("unk", width=0, height=0), cand("small", width=360, height=640),
             cand("port", width=1080, height=1920)]
    best, rest, _ = pick(cands)
    # 直式（高解析度在前）→ 方形、尺寸不明（依相關性順序）→ 橫式
    assert [best, *rest] == ["port", "small", "sq", "unk"]      # 備選只列前 3 個
    assert pick(cands[:3])[:2] == ("sq", ["unk", "land"])
    best, rest, _ = pick([cand("small", width=360, height=640),
                          cand("hd", width=1080, height=1920)])
    assert (best, rest) == ("hd", ["small"])
    assert planner.orientation_of(cand("x", width=1920, height=1080)) == "landscape"
    assert planner.orientation_of(cand("x", width=0, height=1080)) == "unknown"
    _, _, notes = pick([cand("land", width=1920, height=1080)])
    assert "不是直式" in notes[0]
    wants_landscape = planner.Settings(orientation="landscape")
    assert pick(cands, settings=wants_landscape)[0] == "land"


def test_relevance_order_breaks_ties_and_an_asset_is_not_used_twice_unless_it_is_the_last():
    cands = [cand("a:1"), cand("a:2"), cand("a:3")]
    assert pick(cands)[0] == "a:1"
    assert pick(cands, used={"a:1"})[0] == "a:2"
    best, _, notes = pick([cand("a:1")], used={"a:1"})
    assert best == "a:1" and "重複使用" in notes[0]


def test_music_prefers_license_then_long_enough_then_the_longest_of_the_short_ones():
    songs = [cand("m:1", duration=20, kind="free-attribution"), cand("m:2", duration=20),
             cand("m:3", duration=30)]
    best, rest, _ = pick(songs, need=25, music=True)
    assert best == "m:3" and rest == ["m:2", "m:1"]
    best, _, notes = pick([cand("m:1", duration=10), cand("m:2", duration=18)], need=25,
                          music=True)
    assert best == "m:2" and "循環補長" in notes[0]


# ------------------------------------------------------------------ 草稿

def draft(**top):
    return {"id": "t1", "title": "夜市", "scenes": [
        {"text": "台北的夜晚，從一碗熱湯開始。", "query": "night market soup"},
        {"id": "end", "min_duration": 2.0, "query": ["city lights", "taipei"]}], **top}


def test_parse_request_strips_plan_fields_fills_ids_and_keeps_the_manifest_valid():
    req = planner.parse_request(draft(search={"platform": "pixabay", "per_keyword": 4}), CFG)
    assert req.queries == {0: ["night market soup"], 1: ["city lights", "taipei"]}
    assert [s.id for s in req.manifest.scenes] == ["s1", "end"]
    assert "query" not in json.dumps(req.draft) and "search" not in req.draft
    assert req.settings.platform == "pixabay" and req.settings.per_keyword == 4
    assert req.settings.orientation == "portrait"


@pytest.mark.parametrize("change, message", [
    (lambda d: d["scenes"][0].update(query=5), r"scenes\[0\]\.query"),
    (lambda d: d["scenes"][0].update(query=[]), r"scenes\[0\]\.query"),
    (lambda d: d["scenes"][0].update(query=["ok", " "]), r"scenes\[0\]\.query"),
    (lambda d: d["scenes"][0].update(clip={"path": "/x.mp4"}), "clip 與 query 只能給一個"),
    (lambda d: d["scenes"][0].update(txt="拼錯"), r"scenes\[0\]"),
    (lambda d: d.update(search={"platform": "tiktok"}), r"search\.platform"),
    (lambda d: d.update(search={"per_keyword": 0}), r"search\.per_keyword"),
    (lambda d: d.update(search={"allow_unknown_license": "yes"}), r"search\.allow_unknown"),
    (lambda d: d.update(search={"nope": 1}), "不認得的欄位 nope"),
    (lambda d: d.update(search=[1]), "search"),
    (lambda d: d.update(music={"query": "piano", "path": "/a.mp3"}), "music"),
    (lambda d: d.update(music={"query": "piano", "volume": 9}), r"music\.volume"),
    (lambda d: d.update(music={"query": 3}), r"music\.query"),
    (lambda d: d.update(bogus=1), "不認得的欄位 bogus"),
    (lambda d: d.update(id="Bad Id"), "id"),
])
def test_parse_request_reports_the_path_of_a_bad_field(change, message):
    doc = draft()
    change(doc)
    with pytest.raises(BadInput, match=message):
        planner.parse_request(doc, CFG)
    with pytest.raises(BadInput):
        planner.parse_request([doc], CFG)


def test_a_plain_text_script_becomes_scenes_with_optional_keyword_lines():
    text = ("台北的夜晚，從一碗熱湯開始。\n關鍵字：night market, soup\n\n"
            "夜市很熱鬧。\n# street food、crowd\n\n\n# only keywords\n")
    doc = planner.parse_script(text, short_id="t2", title="夜市")
    assert doc["title"] == "夜市" and [s["id"] for s in doc["scenes"]] == ["s1", "s2", "s3"]
    assert doc["scenes"][0] == {"id": "s1", "text": "台北的夜晚，從一碗熱湯開始。",
                                "query": ["night market", "soup"]}
    assert doc["scenes"][1]["query"] == ["street food", "crowd"]
    assert doc["scenes"][2] == {"id": "s3", "min_duration": 2.0, "query": ["only keywords"]}
    planner.parse_request(doc, CFG)          # 產生的草稿本身合法
    with pytest.raises(BadInput, match="腳本是空的"):
        planner.parse_script("  \n\n ", short_id="t2")


# ------------------------------------------------------------------ 流程（假 vidgrab）

@pytest.fixture
def vg(fake_server):
    return FakeVidgrab(fake_server, b"")


def client_for(fake_server):
    return VidgrabClient(fake_server.url, retries=0, sleep=lambda s: None)


def plan(doc, fake_server, cfg=CFG):
    return planner.run(planner.parse_request(doc, cfg), cfg, client=client_for(fake_server))


def stock(body):
    """依關鍵字回不同的候選：夜市有三支，城市燈光一支橫式；音樂兩首。"""
    keywords = " ".join(body["keywords"])
    if body["platform"] == "music":
        return [cand("jamendo:1", duration=200, kind="free-attribution"),
                cand("openverse:2", duration=40, kind="free")]
    if "night" in keywords:
        return [cand("pixabay:10", duration=2, height=1920), cand("pixabay:11", duration=15),
                cand("pixabay:12", duration=15, kind="unknown")]
    return [cand("pixabay:20", duration=8, width=1920, height=1080)]


def test_plan_searches_once_per_query_picks_for_each_scene_and_builds_a_valid_manifest(
        vg, fake_server):
    vg.search_handler = stock
    doc = draft(music={"query": "calm piano", "volume": 0.2})
    doc["scenes"].append({"id": "again", "text": "再來一碗。", "query": "NIGHT MARKET SOUP"})
    result = plan(doc, fake_server)
    manifest, report = result["manifest"], result["report"]

    assert result["schema"] == "vidpipe.plan/1"
    scenes = {s["id"]: s for s in manifest["scenes"]}
    assert scenes["s1"]["clip"] == {"asset_id": "pixabay:11", "start": 0}   # 夠長的那支
    assert scenes["end"]["clip"]["asset_id"] == "pixabay:20"
    assert scenes["again"]["clip"]["asset_id"] == "pixabay:10"               # 11 已經用過
    assert manifest["music"] == {"volume": 0.2, "asset_id": "openverse:2"}   # 40 秒夠長、free
    assert "query" not in json.dumps(manifest) and manifest["id"] == "t1"
    manifest_mod.parse(manifest)                                              # 完整、合法

    assert [(s["platform"], s["keywords"]) for s in report["searches"]] == [
        ("stock", ["night market soup"]), ("stock", ["city lights", "taipei"]),
        ("music", ["calm piano"])]                       # 第三個場景的 query 大小寫不同但只搜一次
    assert [s["keywords"] for s in vg.searches] == [
        ["night market soup"], ["city lights", "taipei"], ["calm piano"]]
    assert vg.searches[0] == {"keywords": ["night market soup"], "platform": "stock",
                              "orientation": "portrait", "per_keyword": 8}

    chosen = report["scenes"][0]["chosen"]
    assert chosen["asset_id"] == "pixabay:11" and chosen["license"]["kind"] == "free"
    assert "直式" in chosen["why"] and "≥" in chosen["why"]
    assert [a["asset_id"] for a in report["scenes"][0]["alternatives"]] == ["pixabay:10"]
    assert report["scenes"][0]["skipped_license"] == 1 and report["scenes"][0][
        "warnings"] == []                                                    # 12 沒列入，但不是警告
    assert report["scenes"][1]["chosen"]["orientation"] == "landscape"
    assert any("end：" in w and "不是直式" in w for w in report["warnings"])
    assert report["music"]["chosen"]["asset_id"] == "openverse:2"
    assert report["without_clip"] == [] and report["estimated_seconds"] > 0


def test_a_scene_with_a_clip_is_left_alone_and_nothing_is_searched_for_it(vg, fake_server):
    vg.search_handler = stock
    doc = draft()
    doc["scenes"][0].pop("query")
    doc["scenes"][0]["clip"] = {"asset_id": "pixabay:11", "start": 3}
    result = plan(doc, fake_server)
    assert result["manifest"]["scenes"][0]["clip"] == {"asset_id": "pixabay:11", "start": 3}
    assert result["report"]["scenes"][0]["clip"] == "given"
    assert [s["keywords"] for s in vg.searches] == [["city lights", "taipei"]]
    # 已經指定的素材也算「用過」，別的場景不會又挑到它
    vg.search_handler = lambda body: [cand("pixabay:11"), cand("pixabay:13")]
    result = plan({**draft(), "scenes": [
        {"id": "a", "text": "第一段。", "clip": {"asset_id": "pixabay:11"}},
        {"id": "b", "text": "第二段。", "query": "x"}]}, fake_server)
    assert result["manifest"]["scenes"][1]["clip"]["asset_id"] == "pixabay:13"


def test_nothing_usable_leaves_the_scene_without_a_clip_and_says_so_but_plans_the_rest(
        vg, fake_server):
    vg.search_handler = lambda body: ([] if "empty" in body["keywords"][0]
                                      else [cand("pixabay:1", kind="unknown")])
    doc = {"id": "t3", "scenes": [
        {"id": "a", "text": "沒有結果。", "query": "empty"},
        {"id": "b", "text": "授權都不能用。", "query": "risky"},
        {"id": "c", "min_duration": 1.0}]}
    result = plan(doc, fake_server)
    assert all("clip" not in s for s in result["manifest"]["scenes"])
    assert result["report"]["without_clip"] == ["a", "b", "c"]
    warnings = " ".join(result["report"]["warnings"])
    assert "a：沒有可用的素材（搜不到）" in warnings
    assert "b：沒有可用的素材（1 支候選的授權不明或要確認" in warnings
    assert "c：沒有旁白也沒有 query" in warnings
    # 放行不明授權：b 選到那支，並且留警告
    doc["search"] = {"allow_unknown_license": True}
    result = plan(doc, fake_server)
    assert result["manifest"]["scenes"][1]["clip"]["asset_id"] == "pixabay:1"
    assert any("公開發佈前要自己確認" in w for w in result["report"]["warnings"])


def test_a_scene_without_a_query_searches_with_its_narration_and_warns(vg, fake_server):
    vg.search_handler = lambda body: [cand("pixabay:1")]
    result = plan({"id": "t4", "scenes": [{"id": "a", "text": "台北的夜晚，從一碗熱湯開始。"}]},
                  fake_server)
    assert vg.searches[0]["keywords"] == ["台北的夜晚，從一碗熱湯開始。"]
    assert "沒有 query" in result["report"]["scenes"][0]["warnings"][0]
    assert result["manifest"]["scenes"][0]["clip"]["asset_id"] == "pixabay:1"


def test_search_settings_reach_vidgrab_and_music_without_a_query_is_kept_as_is(vg, fake_server):
    vg.search_handler = lambda body: [cand("pixabay:1")]
    doc = {"id": "t5", "music": {"path": "/a.mp3", "volume": 0.5},
           "search": {"platform": "pexels", "orientation": "any", "per_keyword": 3},
           "scenes": [{"id": "a", "text": "一段。", "query": "sea"}]}
    result = plan(doc, fake_server)
    assert vg.searches == [{"keywords": ["sea"], "platform": "pexels", "orientation": "any",
                            "per_keyword": 3}]
    assert result["manifest"]["music"] == {"path": "/a.mp3", "volume": 0.5}
    assert result["report"]["music"] is None


def test_a_failed_search_job_is_an_upstream_error_that_names_the_reason(vg, fake_server):
    vg.search_error = "PIXABAY_API_KEY 沒設"
    with pytest.raises(UpstreamError, match="PIXABAY_API_KEY 沒設"):
        plan(draft(), fake_server)


def test_summarize_keeps_the_manifest_and_report_for_the_job_record(vg, fake_server):
    vg.search_handler = stock
    result = plan(draft(), fake_server)
    summary = planner.summarize(result)
    assert set(summary) == {"short_id", "manifest", "report"} and summary["short_id"] == "t1"


# ------------------------------------------------------------------ CLI

@pytest.fixture
def env(tmp_path, fake_server, vg, monkeypatch):
    vg.search_handler = stock
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PYTHONPATH", str(Path(vidpipe.__file__).parent.parent))
    return tmp_path


def test_cli_plan_reads_a_text_script_records_a_job_and_writes_the_manifest(env, vg, capsys):
    script = env / "script.txt"
    script.write_text("台北的夜晚，從一碗熱湯開始。\n關鍵字：night market soup\n\n"
                      "夜市很熱鬧。\n關鍵字：city lights\n", encoding="utf-8")
    out = env / "shorts.json"
    assert cli.main(["plan", str(script), "--id", "t6", "--title", "夜市", "-o", str(out),
                     "--orientation", "any"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] and printed["output"]["path"] == str(out)
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["id"] == "t6" and manifest["title"] == "夜市"
    assert [s["clip"]["asset_id"] for s in manifest["scenes"]] == ["pixabay:11", "pixabay:20"]
    assert vg.searches[0]["orientation"] == "any"
    manifest_mod.parse(manifest)

    state = jobs.get_job(env / "work", printed["job"])
    assert state["kind"] == "plan" and state["status"] == "succeeded"
    assert state["short_id"] == "t6" and state["result"]["manifest"] == manifest
    assert [s["name"] for s in state["stages"]] == ["search"]
    stored = json.loads((env / "work" / "shorts" / "t6" / "plan.json").read_text("utf-8"))
    assert stored["manifest"] == manifest and stored["schema"] == "vidpipe.plan/1"


def test_cli_plan_takes_a_json_draft_and_flags_override_its_search_settings(env, vg, capsys):
    path = env / "draft.json"
    path.write_text(json.dumps(draft(search={"platform": "pexels"})), encoding="utf-8")
    assert cli.main(["plan", str(path), "--platform", "pixabay", "--allow-unknown-license"]) == 0
    assert vg.searches[0]["platform"] == "pixabay"
    capsys.readouterr()


def test_cli_plan_rejects_bad_input_before_starting_a_job(env, vg, capsys):
    script = env / "script.txt"
    script.write_text("一段。\n", encoding="utf-8")
    assert cli.main(["plan", str(script)]) == 2                      # 純文字腳本沒給 --id
    assert "--id" in json.loads(capsys.readouterr().out)["error"]["message"]
    bad = env / "bad.json"
    bad.write_text(json.dumps(draft(search={"platform": "tiktok"})), encoding="utf-8")
    assert cli.main(["plan", str(bad)]) == 2
    assert "search.platform" in json.loads(capsys.readouterr().out)["error"]["message"]
    broken = env / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert cli.main(["plan", str(broken)]) == 2
    capsys.readouterr()
    assert vg.searches == [] and not (env / "work" / "_jobs").exists()


# ------------------------------------------------------------------ HTTP 與 MCP（真的開子行程）

def test_http_plan_runs_as_a_job_and_the_planned_manifest_is_in_its_result(env, vg):
    from test_server import Running

    run = Running(server.make_server("127.0.0.1", 0))
    try:
        bad = run.post("/api/plan", json={"manifest": draft(search={"platform": "tiktok"})})
        assert bad.status_code == 400 and "search.platform" in bad.json()["error"]["message"]
        assert vg.searches == []

        resp = run.post("/api/plan", json={"manifest": draft()})
        assert resp.status_code == 202 and resp.json()["op"] == "plan"
        job_id = resp.json()["job"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = run.get(f"/api/job/{job_id}").json()["job"]
            if job["status"] != "running":
                break
            time.sleep(0.2)
        assert job["status"] == "succeeded", job
        manifest = job["result"]["manifest"]
        assert manifest["scenes"][0]["clip"]["asset_id"] == "pixabay:11"
        assert job["result"]["report"]["scenes"][0]["chosen"]["asset_id"] == "pixabay:11"

        files = {f["name"] for f in run.get("/api/short/t1").json()["short"]["files"]}
        assert "plan.json" in files
        assert run.get("/file/t1/work/plan.json").json()["manifest"] == manifest
        # 規劃出來的 manifest 可以直接拿去 validate
        assert run.post("/api/validate", json={"manifest": manifest}).json()["ok"] is True
    finally:
        run.httpd.shutdown()
        run.httpd.server_close()


SCRIPT = ("台北的夜晚，從一碗熱湯開始。\n關鍵字：night market soup, steaming bowl\n\n"
          "夜市的燈，一盞一盞亮起來。\n關鍵字：city lights")


def test_http_plan_takes_a_plain_text_script_and_the_short_shows_up_as_planned(env, vg):
    """網頁「貼腳本」用的本體：`script` 加 `id`、`title`、`music`、`allow_unknown_license`。"""
    from test_server import Running

    run = Running(server.make_server("127.0.0.1", 0))
    try:
        for body, message in [
            ({"script": SCRIPT}, "缺少 id"),
            ({"script": SCRIPT, "id": "Bad Id"}, "不是合法的作品 id"),
            ({"id": "t2", "script": ""}, "缺少 script"),
            ({"id": "t2", "script": 5}, "script 要是字串"),
            ({"id": "t2", "script": " \n "}, "腳本是空的"),
            ({"id": "t2", "script": SCRIPT, "allow_unknown_license": "yes"},
             "allow_unknown_license 要是 true 或 false"),
        ]:
            resp = run.post("/api/plan", json=body)
            assert resp.status_code == 400 and message in resp.json()["error"]["message"], body
        assert vg.searches == []
        assert run.get("/api/shorts").json()["shorts"] == []        # 壞請求不留下資料夾

        resp = run.post("/api/plan", json={"script": SCRIPT, "id": "t2", "title": "夜市",
                                           "music": "calm piano", "allow_unknown_license": True})
        assert resp.status_code == 202 and resp.json()["short_id"] == "t2"
        job_id = resp.json()["job"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = run.get(f"/api/job/{job_id}").json()["job"]
            if job["status"] != "running":
                break
            time.sleep(0.2)
        assert job["status"] == "succeeded", job

        short = run.get("/api/short/t2").json()["short"]
        assert (short["stage"], short["title"], short["scenes"]) == ("planned", "夜市", 2)
        assert short["manifest_source"] == "plan" and short["manifest"] == job["result"]["manifest"]
        assert short["plan"]["scenes"][0]["chosen"]["asset_id"] == "pixabay:11"
        assert short["plan"]["music"]["chosen"]["asset_id"] == "openverse:2"   # 授權優先
        assert [s["platform"] for s in vg.searches].count("music") == 1
        listed = run.get("/api/shorts").json()["shorts"]
        assert [(s["id"], s["stage"], s["title"]) for s in listed] == [("t2", "planned", "夜市")]
        draft = json.loads((env / "work" / "shorts" / "t2" / "plan-request.json")
                           .read_text(encoding="utf-8"))
        assert draft["search"] == {"allow_unknown_license": True}
        assert draft["music"] == {"query": "calm piano"}
    finally:
        run.httpd.shutdown()
        run.httpd.server_close()


def test_apply_options_layers_flags_on_a_draft_and_leaves_a_non_object_for_the_validator():
    doc = {"id": "a", "search": {"platform": "stock"}, "music": {"volume": 0.2}}
    out = planner.apply_options(doc, music="calm piano", allow_unknown_license=True,
                                orientation="portrait")
    assert out["search"] == {"platform": "stock", "orientation": "portrait",
                             "allow_unknown_license": True}
    assert out["music"] == {"volume": 0.2, "query": "calm piano"}
    assert doc == {"id": "a", "search": {"platform": "stock"}, "music": {"volume": 0.2}}
    assert planner.apply_options(doc) == doc
    assert planner.apply_options([1], music="x") == [1]


def mcp_call(name, arguments, ident=1):
    return mcp.handle_message({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}})["result"]


def test_mcp_plan_short_takes_a_plain_text_script_like_http(env, vg):
    result = mcp_call("plan_short", {"script": SCRIPT, "id": "t3", "title": "夜市",
                                     "music": "calm piano", "wait": 60})
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["done"] and body["short"]["id"] == "t3"
    out = body["job"]["result"]["manifest"]
    assert out["title"] == "夜市" and len(out["scenes"]) == 2
    assert all(scene["clip"]["asset_id"] for scene in out["scenes"])
    assert out["music"]["asset_id"]
    assert [s["keywords"] for s in vg.searches if s["platform"] != "music"][0] == [
        "night market soup", "steaming bowl"]


def test_mcp_plan_short_script_mistakes_are_reported_before_anything_runs(env, vg):
    for args, message in [
        ({"script": SCRIPT}, "缺少 id"),
        ({"script": SCRIPT, "id": "Bad Id"}, "不是合法的作品 id"),
        ({"script": 5, "id": "t3"}, "script 要是字串"),
        ({"script": " \n ", "id": "t3"}, "腳本是空的"),
        ({"script": SCRIPT, "id": "t3", "manifest": draft()}, "只能給一個"),
        ({"id": "t3"}, "缺少 manifest 或 script"),
        ({}, "缺少 manifest 或 script"),
    ]:
        result = mcp_call("plan_short", args)
        assert result["isError"], args
        assert message in result["structuredContent"]["error"]["message"], args
    assert vg.searches == []


def test_mcp_plan_short_schema_no_longer_requires_a_manifest():
    tool = next(t for t in mcp.TOOLS if t["name"] == "plan_short")
    assert tool["inputSchema"]["required"] == []
    assert {"manifest", "script", "id"} <= set(tool["inputSchema"]["properties"])


def test_mcp_plan_short_returns_the_manifest_and_report_and_is_listed(env, vg):
    assert "plan_short" in mcp.HANDLERS and "plan_short" in mcp.INSTRUCTIONS
    reply = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "plan_short", "arguments": {"manifest": draft(), "wait": 60}}})
    result = reply["result"]
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["done"] and body["job"]["kind"] == "plan"
    assert body["job"]["result"]["manifest"]["scenes"][0]["clip"]["asset_id"] == "pixabay:11"
    assert body["short"]["id"] == "t1"
    bad = mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "plan_short", "arguments": {"manifest": draft(search={"x": 1})}}})["result"]
    assert bad["isError"] and "search" in bad["structuredContent"]["error"]["message"]
