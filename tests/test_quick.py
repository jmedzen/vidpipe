"""文案直出：分鏡、主題、批次格式、素材不重複，以及從文案到成片的端到端。"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from mediagen import font_name, has_cjk_font, make, tones
from ttsfake import FakeTTS
from vgfake import FakeVidgrab

from vidpipe import cli, quick
from vidpipe.config import Config
from vidpipe.errors import BadInput

TEXT = """為什麼越想放下,反而越放不下?睡前一躺下，白天的事又自己跑出來。

分手後說要忘記。手卻還是點開他的動態。

今晚念頭又來，就在心裡說一聲，我看到了。然後輕輕鬆手，回到呼吸。"""
DAY = dt.date(2026, 1, 2)


def test_request_defaults_title_id_and_options():
    req = quick.parse_request({"text": TEXT}, today=DAY)
    assert req["title"] == "為什麼越想放下，反而越放不下？"          # 第一句、全形標點
    assert req["id"].startswith("q-20260102-") and len(req["id"]) == 19
    assert req == quick.parse_request({"text": TEXT}, today=DAY)   # 同樣的文案得到同樣的 id
    assert (req["music"], req["allow_unknown_license"], req["subtitle_size"]) == \
        (True, True, "large")
    custom = quick.parse_request({"text": TEXT, "id": "lg-01", "title": "放下", "series": "一分鐘",
                                  "music": "false", "subtitle_size": "xlarge"})
    assert (custom["id"], custom["title"], custom["music"]) == ("lg-01", "放下", False)


@pytest.mark.parametrize("doc, needle", [
    ({}, "text"), ({"text": "，。"}, "text"), ({"text": "好", "txt": 1}, "txt"),
    ({"text": "好", "id": "Bad ID"}, "id"), ({"text": "好", "music": "maybe"}, "music"),
    ({"text": "好", "subtitle_size": "huge"}, "subtitle_size"),
    ({"text": "字" * 1300}, "太長"), ({"text": "畫面：nowhere\n你好。"}, "nowhere")])
def test_bad_requests_say_which_field(doc, needle):
    with pytest.raises(BadInput) as err:
        quick.parse_request(doc)
    assert needle in err.value.message + err.value.hint


def test_storyboard_splits_paragraphs_into_beats_and_picks_themes_by_keyword():
    beats = quick.storyboard(TEXT)
    assert [b["id"] for b in beats] == ["s1-1", "s1-2", "s2-1", "s3-1", "s3-2"]
    assert "".join(b["text"] for b in beats) == quick.normalize(TEXT.replace("\n", ""))
    themes = {b["id"][:2]: b["theme"] for b in beats}
    assert themes["s1"] == "window" and themes["s2"] == "phone"      # 睡前躺下；點開動態
    assert all(quick.chars(b["text"]) <= 32 for b in beats)
    assert beats == quick.storyboard(TEXT)                            # 規則式：結果固定


def test_a_theme_line_overrides_the_keywords_and_neighbours_differ():
    beats = quick.storyboard("畫面：ocean\n手機一直響。\n\n手機又響了。\n\n還是手機。")
    assert [b["theme"] for b in beats][0] == "ocean"
    assert all(a["theme"] != b["theme"] for a, b in zip(beats, beats[1:], strict=False)
               if a["id"][:2] != b["id"][:2])


def test_text_without_keywords_rotates_through_calm_defaults():
    beats = quick.storyboard("甲乙丙丁。\n\n戊己庚辛。\n\n壬癸子丑。")
    assert len({b["theme"] for b in beats}) == 3


def test_batch_accepts_json_jsonl_and_csv():
    rows = [{"id": "a-1", "text": "第一支。", "title": "一"}, {"id": "a-2", "text": "第二支。"}]
    as_json = quick.parse_batch(json.dumps(rows))
    assert [r["id"] for r in as_json] == ["a-1", "a-2"] and as_json[0]["title"] == "一"
    assert quick.parse_batch(json.dumps({"items": rows})) == as_json
    assert quick.parse_batch("\n".join(json.dumps(r) for r in rows), fmt="jsonl") == as_json
    csv_text = '﻿id,title,text,music\na-1,一,第一支。,false\na-2,,"第二支。\n\n有兩段。",\n'
    from_csv = quick.parse_batch(csv_text)
    assert [r["id"] for r in from_csv] == ["a-1", "a-2"]
    assert from_csv[0]["music"] is False and from_csv[1]["music"] is True
    assert "有兩段" in from_csv[1]["text"] and from_csv[1]["title"] == "第二支。"
    shared = quick.parse_batch(rows, shared={"series": "系列", "title": "共用"})
    assert shared[0]["title"] == "一" and shared[1]["title"] == "共用"
    assert all(r["series"] == "系列" for r in shared)


@pytest.mark.parametrize("data, fmt, needle", [
    ("[]", "", "非空"), ("id,title\na,b\n", "csv", "text 欄"), ("{bad", "json", "JSON"),
    (json.dumps([{"text": "一。"}, {"text": "一。"}]), "", "重複"),
    (json.dumps([{"text": "好。"}, {"id": "x"}]), "", "第 2 筆"), ("x", "xml", "xml")])
def test_bad_batches_point_at_the_problem(data, fmt, needle):
    with pytest.raises(BadInput) as err:
        quick.parse_batch(data, fmt=fmt)
    assert needle in err.value.message + err.value.hint


def item(asset_id, kind="free-attribution", duration=300.0):
    return {"id": asset_id, "title": asset_id, "duration": duration, "license": {"kind": kind}}


def test_assign_avoids_back_to_back_repeats_and_spreads_reused_footage():
    beats = [{"id": f"s{i}-1", "text": "一二三四五六七八九十。", "theme": t}
             for i, t in enumerate(["sky", "sky", "sky", "sand", "sand"], 1)]
    pools = {"sky": [item("y:1"), item("y:2"), item("y:unk", "unknown")],
             "sand": [item("y:s", "check", 80)]}
    picks = quick.assign(beats, pools, "seed")
    ids = [p["asset_id"] for p in picks]
    assert "y:unk" not in ids and ids[0] != ids[1] and ids[3:] == ["y:s", "y:s"]
    assert abs(picks[3]["start"] - picks[4]["start"]) >= 4
    assert picks == quick.assign(beats, pools, "seed")
    with pytest.raises(BadInput):
        quick.assign(beats, {"sky": pools["sky"]}, "seed")


def test_used_footage_is_recorded_per_short(tmp_path):
    cfg = Config(work_dir=str(tmp_path))
    quick.record_use(cfg, "a", ["y:1", "y:2"])
    quick.record_use(cfg, "b", ["y:2"])
    quick.record_use(cfg, "a", ["y:3"])                 # 重做 a：舊紀錄換掉
    assert quick.used_assets(cfg) == {"y:2": ["b"], "y:3": ["a"]}


# ------------------------------------------------------------------ 端到端（假 TTS、假 vidgrab）

@pytest.fixture
def world(tmp_path, fake_server, second_server, monkeypatch, capsys):
    FakeTTS(fake_server, speech=lambda text: 0.12 * len(text))
    footage = make(tmp_path / "footage.mp4", "color=c=teal:s=540x960", audio=True, seconds=60,
                   fps=25).read_bytes()
    song = tones(tmp_path / "song.wav", [(220.0, 150.0)], amp=0.25).read_bytes()
    vg = FakeVidgrab(second_server, footage)
    vg.file_bytes = lambda path: song if "openverse" in path else footage

    def search(body):
        n = len(vg.searches)
        if body["platform"] == "music":
            found = [FakeVidgrab.candidate("openverse:1", duration=150, kind="free-attribution")]
        else:                          # 每次搜尋回兩支新的素材（一支標 CC BY、一支未標示）
            found = [FakeVidgrab.candidate(f"youtube:{n}a", duration=60, kind="free-attribution",
                                           width=1920, height=1080),
                     FakeVidgrab.candidate(f"youtube:{n}b", duration=60, kind="unknown",
                                           width=1920, height=1080)]
        for it in found:
            vg.items[it["id"]] = it
        return found

    vg.search_handler = search
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", second_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    if has_cjk_font():
        monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", font_name())

    def run(*argv, expect=0):
        capsys.readouterr()
        code = cli.main([*argv])
        printed = json.loads(capsys.readouterr().out)
        assert code == expect, printed
        return printed

    run.vg, run.root = vg, tmp_path
    return run


def footage_of(root, short_id):
    doc = json.loads((root / "work" / "shorts" / short_id / "manifest.json").read_text("utf-8"))
    return doc, [s["clip"]["asset_id"] for s in doc["scenes"]]


def test_text_to_finished_short_without_any_llm(world):
    out = world("quick", "--text", TEXT, "--id", "lg-01", "--series", "一分鐘佛法")
    assert out["ok"] and out["output"]["path"].endswith("lg-01.mp4")
    assert (world.root / "out" / "lg-01.mp4").stat().st_size > 10_000
    assert (world.root / "out" / "lg-01.cover.jpg").is_file()
    doc, ids = footage_of(world.root, "lg-01")
    assert doc["overlays"][0] == {"text": "為什麼越想放下，反而越放不下？", "position": "top",
                                  "color": "yellow", "size": "xlarge"}
    assert doc["overlays"][1]["text"] == "一分鐘佛法" and doc["subtitles"]["size"] == "large"
    assert doc["music"]["asset_id"] == "openverse:1" and len(doc["scenes"]) == 5
    assert all(i.endswith("a") for i in ids)                        # 授權明確的優先
    assert all(a != b for a, b in zip(ids, ids[1:], strict=False)) or len(set(ids)) < len(ids)
    report = json.loads((world.root / "work" / "shorts" / "lg-01" / "quick-report.json")
                        .read_text("utf-8"))
    assert report["licenses"] == {"free-attribution": 5}


def test_a_second_short_with_the_same_text_gets_new_footage(world):
    world("quick", "--text", TEXT, "--id", "first", "--no-music")
    _, first = footage_of(world.root, "first")
    world("quick", "--text", TEXT, "--id", "second", "--no-music")
    _, second = footage_of(world.root, "second")
    assert not set(first) & set(second)                             # 文案一模一樣，素材完全不重複


def test_dry_run_only_builds_the_manifest(world):
    out = world("quick", "--text", TEXT, "--id", "peek", "--dry-run", "--no-music")
    assert out["manifest"]["id"] == "peek" and len(out["report"]["beats"]) == 5
    assert not (world.root / "out").exists() or not list((world.root / "out").glob("peek*"))


def test_batch_file_runs_every_item_and_reports_failures_separately(world):
    batch = world.root / "batch.csv"
    batch.write_text('id,title,text\nb-1,第一支,"今天很好。\n\n明天也好。"\n'
                     'b-2,,"畫面：ocean\n海很大。\n\n浪很高。"\n', encoding="utf-8")
    out = world("quick", str(batch), "--no-music")
    assert out["output"]["total"] == 2 and out["output"]["failed"] == []
    assert (world.root / "out" / "b-1.mp4").is_file() and (world.root / "out" / "b-2.mp4").is_file()
    _, one = footage_of(world.root, "b-1")
    _, two = footage_of(world.root, "b-2")
    assert not set(one) & set(two)


def test_footage_that_cannot_be_fetched_is_banned_and_replaced(world):
    def on_batch(body):                       # 第一支挑到的素材抓不下來
        if body["asset_ids"][0] == "youtube:1a":
            return 200, {"schema": "vidgrab.batch/1", "project": body["project"],
                         "results": [{"asset_id": "youtube:1a", "new_clips": [],
                                      "error": "ERROR: [youtube] 1a: This video is not available"}]}
        return world.vg._default_batch(body)

    world.vg.on_batch = on_batch
    out = world("quick", "--text", "今天很好。", "--id", "retry-1", "--no-music")
    _, ids = footage_of(world.root, "retry-1")
    assert "youtube:1a" not in ids and out["output"]["path"].endswith("retry-1.mp4")
    assert any("抓不下來" in w for w in out["output"]["warnings"])
    cfg = Config(work_dir=str(world.root / "work"))
    assert "youtube:1a" in quick.bad_assets(cfg)
    message = "vidgrab 抓不到 youtube:yrfJRKqJc-w：ERROR: …"
    assert quick.failed_asset(message) == "youtube:yrfJRKqJc-w"
    assert quick.failed_asset("tts 連不上") is None
