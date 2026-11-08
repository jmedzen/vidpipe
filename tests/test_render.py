"""render：真的跑 ffmpeg，看成片的畫面、聲音、幀數、快取。TTS 與 vidgrab 用假服務。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from mediagen import (
    bright_rows,
    color_clip,
    count_frames,
    dominant,
    font_name,
    has_cjk_font,
    peak,
    pixel,
)
from ttsfake import FakeTTS
from vgfake import FakeVidgrab

from vidpipe import cli, ffmpeg, jobs, render

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

FPS = 10   # 測試用低幀率，編碼快；時間軸一樣會對齊到整數幀
CENTER = (540, 960)


def manifest(scenes, **top):
    return {"schema": "vidpipe.manifest/1", "id": "r1", "output": {"fps": FPS},
            "subtitles": {"burn_in": False}, **top, "scenes": scenes}


@pytest.fixture
def env(tmp_path, fake_server, monkeypatch, capsys):
    """TTS 假服務＋工作目錄；回一組小工具。"""
    FakeTTS(fake_server, speech=lambda text: 0.1 * len(text))
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))

    class Env:
        root = tmp_path / "work" / "shorts" / "r1"
        manifest_path = tmp_path / "shorts.json"
        work = tmp_path / "work"

        def write(self, data):
            self.manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        def cli(self, *argv, expect=0):
            capsys.readouterr()
            code = cli.main([*argv])
            out = json.loads(capsys.readouterr().out)
            assert code == expect, out
            return out

        def narrate(self, data=None):
            if data is not None:
                self.write(data)
            return self.cli("narrate", str(self.manifest_path))

        def render(self, *extra, expect=0):
            return self.cli("render", str(self.manifest_path), *extra, expect=expect)

        def timeline(self):
            return json.loads((self.root / "timeline.json").read_text(encoding="utf-8"))

    return Env()


@pytest.fixture
def clips(tmp_path):
    """四秒的紅、藍單色影片（都有聲音，用來確認原片聲音沒有漏進成片）。"""
    return {c: color_clip(tmp_path / f"{c}.mp4", [c], seg=4, fps=FPS) for c in ("red", "blue")}


def seg_names(env):
    return sorted(p.name for p in (env.root / "render" / "segments").glob("*.mp4"))


# ------------------------------------------------------------------ 端到端

def test_renders_a_9x16_short_with_exact_frames_right_pictures_and_narration(env, clips):
    env.narrate(manifest([
        {"id": "a", "text": "你好呀。", "clip": {"path": str(clips["red"])}},
        {"id": "b", "text": "晚安。", "clip": {"path": str(clips["blue"])}},
        {"id": "c", "min_duration": 1.0}]))
    out = env.render()
    result = out["output"]
    tl = env.timeline()
    video = result["path"]

    assert video.endswith("out/r1.mp4")
    assert (result["width"], result["height"], result["fps"]) == (1080, 1920, FPS)
    assert count_frames(video) == sum(s["frames"] for s in tl["scenes"]) == tl["total_frames"]
    assert result["duration"] == pytest.approx(tl["total"], abs=0.15)
    assert ffmpeg.probe(video)["has_audio"]
    assert result["burned_subtitles"] is False and result["warnings"] == [
        "場景 c 沒有 clip，用黑畫面"]

    a, b, c = tl["scenes"]
    assert dominant(pixel(video, *CENTER, at=a["start"] + a["duration"] / 2)) == "r"
    assert dominant(pixel(video, *CENTER, at=b["start"] + b["duration"] / 2)) == "b"
    assert sum(pixel(video, *CENTER, at=c["start"] + 0.5)) < 40
    # 旁白在句子的位置有聲音；沒有旁白的場景是靜音（藍紅原片的聲音沒有漏進來）
    first = a["sentences"][0]
    assert peak(video, first["start"] + 0.1, first["end"] - first["start"] - 0.2) > 3000
    assert peak(video, c["start"] + 0.1, c["duration"] - 0.2) < 300

    state = jobs.get_job(env.work, out["job"])
    assert state["kind"] == "render" and state["short_id"] == "r1"
    assert [s["name"] for s in state["stages"]] == ["assets", "segments", "mux"]
    assert state["status"] == "succeeded"
    assert not list((env.root / "render").rglob(".*partial*"))


def test_output_option_chooses_the_file_and_no_burn_in_gives_an_srt_beside_it(
        env, clips, tmp_path):
    env.narrate(manifest([{"id": "a", "text": "你好呀。", "clip": {"path": str(clips["red"])}}]))
    dest = tmp_path / "deliver" / "night.mp4"
    result = env.render("-o", str(dest))["output"]
    assert result["path"] == str(dest) and dest.is_file()
    assert result["srt"] == str(dest.with_suffix(".srt"))
    assert dest.with_suffix(".srt").read_text(encoding="utf-8") == (
        env.root / "subs.srt").read_text(encoding="utf-8")
    info = ffmpeg.probe(dest)
    assert info["has_audio"] and info["has_video"]


@pytest.mark.skipif(not has_cjk_font(), reason="需要 libass 與一個繁中字體")
def test_burned_in_subtitles_appear_at_the_bottom_while_the_sentence_is_spoken(
        env, monkeypatch, tmp_path):
    monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", font_name())
    dark = color_clip(tmp_path / "dark.mp4", ["0x202020"], seg=4, fps=FPS, audio=False)
    env.narrate(manifest(
        [{"id": "a", "text": "台北的夜晚，從一碗熱湯開始。", "clip": {"path": str(dark)}}],
        subtitles={"burn_in": True}))
    result = env.render()["output"]
    assert result["burned_subtitles"] is True and result["srt"] is None
    first = env.timeline()["scenes"][0]["sentences"][0]
    rows = bright_rows(result["path"], (first["start"] + first["end"]) / 2, 1080, 1920)
    assert rows and min(rows) > 1920 * 0.5     # 在下半部
    assert not bright_rows(result["path"], 0.0, 1080, 1920)   # 還沒開口，沒有字


def test_a_silent_manifest_still_gets_a_silent_audio_track(env, clips):
    env.narrate(manifest([
        {"id": "a", "min_duration": 1.0, "clip": {"path": str(clips["red"])}},
        {"id": "b", "min_duration": 1.0, "clip": {"path": str(clips["blue"])},
         "mute_source": False}]))
    result = env.render()["output"]
    assert not (env.root / "narration.wav").exists()
    info = ffmpeg.probe(result["path"])
    assert info["has_audio"] and info["duration"] == pytest.approx(2.0, abs=0.15)
    assert peak(result["path"], 0.1, 1.8) < 300
    assert result["warnings"] == ["場景 b 的 mute_source=false 還沒支援，先靜音"]


# ------------------------------------------------------------------ 快取與過期

def test_rerendering_reuses_every_segment_and_changing_one_scene_redoes_only_it(env, clips):
    data = manifest([
        {"id": "a", "text": "你好呀。", "clip": {"path": str(clips["red"])}},
        {"id": "b", "text": "晚安。", "clip": {"path": str(clips["blue"])}},
        {"id": "c", "min_duration": 1.0}])
    env.narrate(data)
    assert [s["cached"] for s in env.render()["output"]["scenes"]] == [False, False, False]
    before = seg_names(env)
    assert len(before) == 3
    assert [s["cached"] for s in env.render()["output"]["scenes"]] == [True, True, True]
    assert seg_names(env) == before

    data["scenes"][1]["clip"]["start"] = 1      # 只動場景 b 的起點
    env.narrate(data)
    scenes = env.render()["output"]["scenes"]
    assert [s["cached"] for s in scenes] == [True, False, True]
    after = seg_names(env)
    assert len(after) == 3                       # 舊版本的 b 被清掉
    assert [n for n in before if n not in after] == [n for n in before if "-b-" in n]


def test_a_manifest_changed_after_narrate_is_refused_until_narrate_runs_again(env, clips):
    data = manifest([{"id": "a", "text": "你好呀。", "clip": {"path": str(clips["red"])}}])
    env.narrate(data)
    data["scenes"][0]["text"] = "你好嗎。"
    env.write(data)
    err = env.render(expect=2)["error"]
    assert err["code"] == "bad_input" and "narrate" in err["hint"]
    assert not (env.work.parent / "out").exists()
    env.narrate()
    assert env.render()["ok"] is True


def test_render_before_narrate_asks_for_narrate(env, clips):
    env.write(manifest([{"id": "a", "text": "你好呀。", "clip": {"path": str(clips["red"])}}]))
    err = env.render(expect=1)["error"]
    assert err["code"] == "not_found" and "narrate" in err["hint"]


# ------------------------------------------------------------------ 素材的各種狀況

def test_a_source_shorter_than_the_scene_loops_and_says_so(env, tmp_path):
    short = color_clip(tmp_path / "short.mp4", ["red"], seg=0.5, fps=FPS, audio=False)
    env.narrate(manifest([{"id": "a", "min_duration": 2.0, "clip": {"path": str(short)}}]))
    result = env.render()["output"]
    scene = result["scenes"][0]
    assert scene["looped"] is True and scene["frames"] == 20
    assert count_frames(result["path"]) == 20
    assert dominant(pixel(result["path"], *CENTER, at=1.8)) == "r"   # 後面不是黑的也不是凍結
    assert len(result["warnings"]) == 1 and "循環補長" in result["warnings"][0]


def test_a_clip_start_past_the_end_of_the_source_names_the_scene(env, clips):
    env.narrate(manifest([
        {"id": "a", "min_duration": 1.0},
        {"id": "b", "min_duration": 1.0, "clip": {"path": str(clips["red"]), "start": 99}}]))
    err = env.render(expect=2)["error"]
    assert "scenes[1].clip" in err["message"]


def test_a_missing_clip_file_names_the_scene(env, tmp_path):
    env.narrate(manifest([{"id": "a", "min_duration": 1.0,
                           "clip": {"path": str(tmp_path / "nope.mp4")}}]))
    err = env.render(expect=1)["error"]
    assert err["code"] == "not_found" and "scenes[0].clip" in err["message"]


# ------------------------------------------------------------------ vidgrab 素材

@pytest.fixture
def vg(second_server, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", second_server.url)
    video = color_clip(tmp_path / "grabbed.mp4", ["red"], seg=4, fps=FPS, audio=False)
    fake = FakeVidgrab(second_server, video.read_bytes())
    fake.add("pixabay:1", duration=20.0)
    return fake


ASSET_SCENE = {"id": "a", "text": "你好呀。", "clip": {"asset_id": "pixabay:1", "start": 3}}


def test_an_asset_clip_asks_vidgrab_for_only_the_needed_range_and_is_reused_next_time(
        env, vg):
    env.narrate(manifest([ASSET_SCENE]))
    result = env.render()["output"]
    scene = env.timeline()["scenes"][0]
    assert len(vg.batches) == 1
    start, end = vg.batches[0]["ranges"][0]
    assert start == 3 and end == pytest.approx(3 + scene["duration"] + 0.5, abs=0.01)
    assert vg.batches[0]["frames"] is False
    assert result["scenes"][0]["source"] == "pixabay:1" and result["warnings"] == []
    assert dominant(pixel(result["path"], *CENTER, at=0.5)) == "r"

    sources = json.loads((env.root / "sources.json").read_text(encoding="utf-8"))
    assert sources["schema"] == "vidpipe.sources/1" and sources["short_id"] == "r1"
    entry = sources["scenes"][0]
    assert entry["scene"] == "a" and entry["offset"] == 0
    assert entry["source"]["asset_id"] == "pixabay:1"
    assert entry["source"]["author"] == "Ann" and entry["source"]["credit_hint"]

    again = env.render()["output"]
    assert len(vg.batches) == 1 and len(vg.downloads) == 1     # 沒有重抓、沒有重下載
    assert again["scenes"][0]["cached"] is True


def test_an_asset_with_an_unclear_license_gets_a_warning(env, vg):
    vg.items["pixabay:1"]["license"] = {"kind": "check", "label": "請確認授權"}
    env.narrate(manifest([ASSET_SCENE]))
    warnings = env.render()["output"]["warnings"]
    assert len(warnings) == 1 and "授權" in warnings[0] and "pixabay:1" in warnings[0]


# ------------------------------------------------------------------ 路徑跳脫

def test_filter_value_quotes_everything_ffmpeg_filters_treat_as_special():
    assert render.filter_value("/a/b.ass") == "'/a/b.ass'"
    assert render.filter_value("/a b/c.ass") == "'/a b/c.ass'"
    assert render.filter_value("C:\\x") == "'C\\:\\\\x'"
    assert render.filter_value("/it's/x") == "'/it\\'\\''s/x'"


def test_paths_full_of_filter_syntax_characters_still_burn_subtitles(
        tmp_path, fake_server, monkeypatch, capsys, clips):
    FakeTTS(fake_server, speech=lambda text: 0.1 * len(text))
    work = tmp_path / "a b:c'd,[e];f"
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(work))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest(
        [{"id": "a", "text": "你好呀。", "clip": {"path": str(clips["red"])}}],
        subtitles={"burn_in": True}), ensure_ascii=False), encoding="utf-8")
    assert cli.main(["narrate", str(path)]) == 0
    capsys.readouterr()
    assert cli.main(["render", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)["output"]
    assert result["burned_subtitles"] is True
    tl = json.loads((work / "shorts" / "r1" / "timeline.json").read_text(encoding="utf-8"))
    assert count_frames(result["path"]) == tl["total_frames"]


def test_ffmpeg_really_opens_an_ass_file_through_filter_value(tmp_path):
    ass = tmp_path / "x y:z'w,[v];u" / "s.ass"
    ass.parent.mkdir()
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n",
                   encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=320x240:d=0.2:r=10", "-filter_complex",
                    f"[0:v]ass={render.filter_value(str(ass))}[v]", "-map", "[v]",
                    str(tmp_path / "o.mp4")], check=True)
    assert (tmp_path / "o.mp4").is_file()
