"""端到端驗收：一份固定腳本（examples/taipei-night）走完 plan → validate → make，
得到一支 30–45 秒的 Short。TTS 與 vidgrab 都是假的，素材是合成的；真的素材與真的 TTS
要在使用者的 Mac 上跑（見 docs/agent-recipe.md 的「本機驗收」）。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from mediagen import bright_rows, font_name, has_cjk_font, make, peak, tones
from ttsfake import FakeTTS
from vgfake import FakeVidgrab

from vidpipe import cli, ffmpeg, jobs, mix
from vidpipe import manifest as manifest_mod

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "examples" / "taipei-night" / "script.txt"
RECIPE = ROOT / "docs" / "agent-recipe.md"
SHORT_ID = "taipei-night-01"
SPEECH_SECONDS_PER_CHAR = 0.2          # 約等於真人語速（每秒 4–5 字）
SOURCE_SECONDS = 10.0


@pytest.fixture
def world(tmp_path, fake_server, second_server, monkeypatch, capsys):
    """假 TTS、假 vidgrab（搜尋回合成的候選，下載回合成的影片與音樂）、乾淨的工作目錄。"""
    tts = FakeTTS(fake_server, speech=lambda text: SPEECH_SECONDS_PER_CHAR * len(text))
    footage = make(tmp_path / "footage.mp4", "color=c=teal:s=540x960", audio=True,
                   seconds=SOURCE_SECONDS, fps=25).read_bytes()
    song = tones(tmp_path / "song.wav", [(220.0, 50.0)], amp=0.25).read_bytes()
    vg = FakeVidgrab(second_server, footage)
    vg.file_bytes = lambda path: song if "jamendo" in path else footage

    def search(body):
        if body["platform"] == "music":
            found = [FakeVidgrab.candidate("jamendo:1", duration=60, kind="free-attribution"),
                     FakeVidgrab.candidate("jamendo:2", duration=20, kind="free-attribution")]
        else:
            n = len(vg.searches)                        # 每次搜尋各自的素材，場景之間不會重複
            found = [FakeVidgrab.candidate(f"pixabay:{n}1", duration=SOURCE_SECONDS),
                     FakeVidgrab.candidate(f"pixabay:{n}2", duration=SOURCE_SECONDS,
                                           width=1920, height=1080),
                     FakeVidgrab.candidate(f"pixabay:{n}3", duration=SOURCE_SECONDS,
                                           kind="unknown")]
        for item in found:
            vg.items[item["id"]] = item
        return found

    vg.search_handler = search
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", second_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "out"))
    if has_cjk_font():
        monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", font_name())

    class World:
        root = tmp_path
        out = tmp_path / "out"
        work = tmp_path / "work" / "shorts" / SHORT_ID

        def __init__(self):
            self.vg, self.tts = vg, tts

        def cli(self, *argv, expect=0):
            capsys.readouterr()
            code = cli.main([*argv])
            printed = json.loads(capsys.readouterr().out)
            assert code == expect, printed
            return printed

    return World()


@pytest.mark.skipif(not has_cjk_font(), reason="燒字幕需要 libass 與一個繁中字體")
def test_the_fixed_script_becomes_a_30_to_45_second_9x16_short_with_everything_around_it(world):
    # 1. plan：純文字腳本（每段的「關鍵字：」是搜尋用的）→ manifest
    manifest_path = world.root / "shorts.json"
    planned = world.cli("plan", str(SCRIPT), "--id", SHORT_ID, "--title", "台北夜市",
                        "--music", "calm piano", "-o", str(manifest_path))["output"]
    report = planned["report"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == planned["manifest"] and manifest_mod.parse(manifest).id == SHORT_ID
    assert len(manifest["scenes"]) == 8 and report["without_clip"] == []
    assert report["warnings"] == []                              # 沒有授權或長度的疑慮
    assert [s["chosen"]["license"]["kind"] for s in report["scenes"]] == ["free"] * 8
    assert all(s["chosen"]["orientation"] == "portrait" for s in report["scenes"])
    assert len({s["clip"]["asset_id"] for s in manifest["scenes"]}) == 8
    assert manifest["music"] == {"asset_id": "jamendo:1"}        # 夠長的那首
    assert [s["keywords"] for s in report["searches"]][0] == ["night market soup",
                                                              "steaming bowl"]

    # 2. validate：不呼叫 TTS 就知道大概多長
    checked = world.cli("validate", str(manifest_path))
    assert 25 < checked["estimated_seconds"] < 50 and world.tts.calls == []

    # 3. make：旁白、時間軸、渲染、封面、credits
    made = world.cli("make", str(manifest_path))
    result = made["output"]
    video = Path(result["path"])
    assert video == world.out / f"{SHORT_ID}.mp4" and result["warnings"] == []
    info = ffmpeg.probe(video)
    assert 30 <= info["duration"] <= 45, info["duration"]
    assert (info["width"], info["height"], info["fps"]) == (1080, 1920, 30)
    assert info["has_audio"] and info["has_video"]

    state = jobs.get_job(world.root / "work", made["job"])
    assert state["status"] == "succeeded"
    assert [s["name"] for s in state["stages"]] == [
        "narrate", "timeline", "assets", "segments", "audio", "mux"]

    # 4. 聲音：旁白＋音樂混好、響度標準化到 -14 LUFS
    assert result["audio"]["music"]["ducked"] is True
    loudness = mix.measure(video)
    assert loudness is not None and loudness["i"] == pytest.approx(-14.0, abs=1.5)
    assert peak(video, 1.0, 1.0, band=440.0) > 500             # 旁白在
    assert peak(video, 1.0, 1.0, band=220.0) > 0               # 音樂也在

    # 5. 字幕預設燒進畫面（下半部、有人說話時才有），也留下字幕檔
    tl = json.loads((world.work / "timeline.json").read_text(encoding="utf-8"))
    first = tl["scenes"][0]["sentences"][0]
    rows = bright_rows(video, (first["start"] + first["end"]) / 2, 1080, 1920)
    assert rows and min(rows) > 1920 * 0.5
    assert result["burned_subtitles"] is True
    ass = (world.work / "subs.ass").read_text(encoding="utf-8")
    assert "台北的夜晚，從一碗熱湯開始。" in ass
    assert sum(len(s["sentences"]) for s in tl["scenes"]) == ass.count("Dialogue:")

    # 6. 封面與素材來源清單
    cover = world.out / f"{SHORT_ID}.cover.jpg"
    cover_info = ffmpeg.probe(cover)
    assert (cover_info["width"], cover_info["height"]) == (1080, 1920)
    credits = (world.out / f"{SHORT_ID}.credits.md").read_text(encoding="utf-8")
    for scene in manifest["scenes"]:
        assert scene["clip"]["asset_id"] in credits
    assert "jamendo:1" in credits and "Ann" in credits and "可自由使用" in credits
    sources = json.loads((world.work / "sources.json").read_text(encoding="utf-8"))
    assert len(sources["scenes"]) == 8 and sources["music"]["looped"] is False

    # 7. 重跑一次：旁白與每個場景都走快取，成品不變
    tts_calls = len(world.tts.calls)
    assert tts_calls > 0
    again = world.cli("make", str(manifest_path))["output"]
    assert [s["cached"] for s in again["scenes"]] == [True] * 8
    assert len(world.tts.calls) == tts_calls
    assert ffmpeg.probe(again["path"])["duration"] == pytest.approx(info["duration"], abs=0.05)

    # 8. 這份腳本的字數確實落在 30–45 秒（防止之後改了範例卻沒發現驗收失效）
    spoken = re.sub(r"\s", "", "".join(
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines(True)
        if not line.startswith("關鍵字")))
    assert 30 <= len(spoken) * SPEECH_SECONDS_PER_CHAR + 8 * 0.6 <= 45


def test_the_draft_shown_in_the_agent_recipe_is_a_valid_plan_request_and_the_script_parses():
    """文件裡的範例要保持是對的：改了 manifest 或 plan 的欄位而忘了改文件，這裡會紅。"""
    from vidpipe import planner
    from vidpipe.config import Config

    block = re.search(r"```json\n(.*?)\n```", RECIPE.read_text(encoding="utf-8"), re.S)
    assert block, "recipe 裡找不到 json 範例"
    req = planner.parse_request(json.loads(block.group(1)), Config())
    assert req.manifest.id == SHORT_ID and sorted(req.queries) == [0, 1]
    assert req.music_query == ["calm piano"] and req.music_extra == {"volume": 0.3}
    doc = planner.parse_script(SCRIPT.read_text(encoding="utf-8"), short_id=SHORT_ID)
    assert len(planner.parse_request(doc, Config()).queries) == 8
