"""本機背景音樂曲庫：掃描、標籤、輪流挑歌、改授權，以及文案直出、網頁 API 怎麼用它。"""
from __future__ import annotations

import json

import pytest
from mediagen import tones

from vidpipe import bgm, cli, manifest, quick
from vidpipe.config import Config
from vidpipe.errors import BadInput, NotFound


@pytest.fixture(scope="module")
def song(tmp_path_factory):
    return tones(tmp_path_factory.mktemp("src") / "song.wav", [(330.0, 3.0)]).read_bytes()


@pytest.fixture
def lib(tmp_path, song):
    root = tmp_path / "bgm"
    for rel in ("古琴/流水.wav", "古琴/高山.wav", "明亮/晨光.wav", "鋼琴.wav",
                "_停用/舊歌.wav", ".hidden/x.wav"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(song)
    (root / "古琴" / "流水.json").write_text(json.dumps({
        "author": "某琴人", "license": "free-attribution", "url": "https://example.com/l",
        "tags": ["夜晚", "古琴"], "start": 1}, ensure_ascii=False), encoding="utf-8")
    (root / "說明.txt").write_text("不是音檔", encoding="utf-8")
    return Config(bgm_dir=str(root))


def test_scan_reads_folders_as_tags_and_the_sidecar_json(lib):
    tracks = {t["id"]: t for t in bgm.scan(lib)}
    assert set(tracks) == {"古琴/流水.wav", "古琴/高山.wav", "明亮/晨光.wav", "鋼琴.wav"}
    water = tracks["古琴/流水.wav"]
    assert water["tags"] == ["古琴", "夜晚"] and water["license"] == "free-attribution"
    assert water["credit_text"] == "某琴人《流水》 — https://example.com/l"
    assert water["start"] == 1 and 2.9 < water["duration"] < 3.1
    assert tracks["鋼琴.wav"]["tags"] == [] and tracks["鋼琴.wav"]["license"] == "unknown"
    assert bgm.tags_of(list(tracks.values())) == [
        {"tag": "古琴", "count": 2}, {"tag": "夜晚", "count": 1}, {"tag": "明亮", "count": 1}]
    assert bgm.scan(Config(bgm_dir=str(lib.bgm_dir) + "-nope")) == []


def test_each_short_gets_the_least_recently_used_track_and_keeps_it_on_redo(lib):
    first = [bgm.pick(lib, sid)["track"]["id"] for sid in ("a", "b", "c", "d")]
    assert len(set(first)) == 4                                   # 四首都輪到，沒有重複
    again = bgm.pick(lib, "b")
    assert again["reused"] and again["track"]["id"] == first[1]   # 重做同一支片：同一首
    assert bgm.pick(lib, "e")["track"]["id"] == first[0]          # 第五支：最久沒用的那首
    listed = {t["id"]: t for t in bgm.scan(lib)}
    assert listed[first[0]]["uses"] == 2 and listed[first[0]]["last_used_by"] == "e"


def test_tags_filter_and_a_name_picks_one_track(lib):
    warnings: list[str] = []
    assert bgm.pick(lib, "x", "古琴", warnings=warnings)["track"]["tags"][0] == "古琴"
    assert bgm.pick(lib, "y", "明亮,夜晚")["track"]["id"] in ("明亮/晨光.wav", "古琴/流水.wav")
    assert bgm.pick(lib, "z", "高山")["track"]["id"] == "古琴/高山.wav"
    assert not any("沒有標籤" in w for w in warnings)
    got = bgm.pick(lib, "w", "搖滾", warnings=warnings)
    assert got and any("搖滾" in w for w in warnings)        # 沒有這個標籤：從全部挑並警告
    assert bgm.pick(Config(bgm_dir=str(lib.bgm_dir) + "-empty"), "a") is None


def test_dry_run_does_not_touch_the_rotation(lib):
    peek = bgm.pick(lib, "p", record=False)["track"]["id"]
    assert bgm.pick(lib, "p", record=False)["track"]["id"] == peek
    assert not (bgm.root(lib) / ".vidpipe" / "history.json").exists()


def test_the_picked_track_becomes_a_valid_manifest_music_with_its_license(lib):
    warnings: list[str] = []
    water = bgm.pick(lib, "a", "流水", warnings=warnings)["music"]
    assert water["license"] == "free-attribution" and "某琴人" in water["credit"]
    assert water["start"] == 1 and water["volume"] == bgm.DEFAULT_VOLUME
    piano = bgm.pick(lib, "b", "鋼琴", warnings=warnings)["music"]
    assert "license" not in piano and "沒有標示授權" in warnings[-1]
    for music in (water, piano):
        doc = {"schema": manifest.SCHEMA, "id": "m", "scenes": [{"id": "s", "text": "你好。"}],
               "music": music}
        assert manifest.parse(doc).music["path"] == music["path"]


def test_update_writes_the_sidecar_and_checks_values(lib):
    t = bgm.update(lib, "鋼琴.wav", {"license": "own", "tags": "夜晚，舒緩", "volume": 0.3})
    assert (t["license"], t["tags"], t["volume"]) == ("own", ["夜晚", "舒緩"], 0.3)
    side = json.loads((bgm.root(lib) / "鋼琴.json").read_text("utf-8"))
    assert side == {"license": "own", "tags": ["夜晚", "舒緩"], "volume": 0.3}
    assert bgm.update(lib, "鋼琴.wav", {"enabled": False})["enabled"] is False
    assert all(t["id"] != "鋼琴.wav" for t in bgm.matching(bgm.scan(lib), []))
    bgm.update(lib, "鋼琴.wav", {"license": "unknown", "tags": [], "volume": 0.22,
                                 "enabled": True})
    assert json.loads((bgm.root(lib) / "鋼琴.json").read_text("utf-8")) == {"volume": 0.22}
    for bad in ({"license": "cc"}, {"volume": 5}, {"enabled": "yes"}, {"color": "red"},
                {"license": "free-attribution"}):
        with pytest.raises(BadInput):
            bgm.update(lib, "鋼琴.wav", bad)


@pytest.mark.parametrize("track_id", ["../x.wav", "/etc/passwd", "說明.txt", ".hidden/x.wav",
                                      "古琴/沒有.wav", ""])
def test_find_only_returns_audio_inside_the_library(lib, track_id):
    with pytest.raises(NotFound):
        bgm.find(lib, track_id)


def test_cli_lists_and_picks(lib, monkeypatch, capsys):
    monkeypatch.setenv("VIDPIPE_BGM_DIR", lib.bgm_dir)
    assert cli.main(["bgm", "list", "--tags", "古琴"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {t["id"] for t in listed["tracks"]} == {"古琴/流水.wav", "古琴/高山.wav"}
    assert listed["usable"] == 4
    assert cli.main(["bgm", "pick", "--id", "lg-01", "--tags", "明亮"]) == 0
    picked = json.loads(capsys.readouterr().out)
    assert picked["track"]["id"] == "明亮/晨光.wav" and picked["music"]["path"].endswith("晨光.wav")
    assert cli.main(["bgm", "set", "鋼琴.wav", "--license", "free", "--tags", "鋼琴,明亮"]) == 0
    assert json.loads(capsys.readouterr().out)["track"]["tags"] == ["鋼琴", "明亮"]


def test_init_creates_the_folder_with_a_readme(tmp_path):
    cfg = Config(bgm_dir=str(tmp_path / "new"))
    assert bgm.init(cfg)["created"] is True
    assert (tmp_path / "new" / bgm.README_NAME).is_file()
    assert bgm.init(cfg)["created"] is False


def test_quick_uses_the_library_first_and_music_can_be_a_tag(lib, monkeypatch):
    def no_vidgrab(*a, **k):
        raise AssertionError("曲庫有歌就不該去 vidgrab 找音樂")

    monkeypatch.setattr(quick, "pick_music", no_vidgrab)
    monkeypatch.setattr(quick, "pick_footage", lambda cfg, sid, beats, **kw: (
        [{"scene": b["id"], "asset_id": "youtube:a", "start": 0.0,
          "item": {"license": {"kind": "free"}}} for b in beats], []))
    req = quick.parse_request({"text": "今天很好。\n\n明天也好。", "id": "q-1", "music": "古琴"})
    assert req["music"] == "古琴"
    built = quick.build(req, lib, client=object())
    assert built["manifest"]["music"]["path"].startswith(str(bgm.root(lib) / "古琴"))
    assert built["report"]["music"]["source"] == "library"
    plain = quick.build(quick.parse_request({"text": "今天很好。", "id": "q-2"}), lib,
                        client=object())
    assert plain["report"]["music"]["source"] == "library"
