from __future__ import annotations

import json
import shutil
import wave

import pytest
from ttsfake import FakeTTS

from vidpipe import cli, jobs, manifest, timeline
from vidpipe.config import Config
from vidpipe.errors import BadInput
from vidpipe.narration import Clip

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

RATE = 24000


def parse(scenes, **top):
    return manifest.parse({"schema": "vidpipe.manifest/1", "id": "t1", "scenes": scenes, **top})


def clips_for(m, durations, directory=None):
    """依序給每一句一個長度的假 Clip（不碰 TTS）。"""
    texts = timeline.sentence_texts(m)
    assert len(texts) == len(durations)
    return [Clip(i, t, f"{directory or '/cache'}/{i}.wav", d, False)
            for i, (t, d) in enumerate(zip(texts, durations, strict=True))]


def tone(path, seconds, value=1000, rate=RATE, channels=1):
    """整段都是固定非零值的 WAV，方便檢查「這個位置有沒有聲音」。"""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(value.to_bytes(2, "little", signed=True) * channels * round(seconds * rate))
    return str(path)


# ------------------------------------------------------------------ 排時間


def test_scene_timing_follows_lead_gap_and_tail():
    m = parse([{"id": "a", "sentences": [{"text": "一。"}, {"text": "二。"}]},
               {"id": "b", "min_duration": 2.0}])
    tl = timeline.build(m, clips_for(m, [1.0, 0.5]))
    a, b = tl.scenes
    first, second = a.cues
    assert (first.start, first.end) == (0.15, 1.15)            # lead 0.15
    assert (second.start, second.end) == (1.4, 1.9)             # gap 0.25
    # 1.9 + tail 0.45 = 2.35 秒 → 向上取整到 71 幀（30fps）
    assert (a.frames, a.duration) == (71, 2.367) and a.speech_end == 1.9
    assert (b.start_frame, b.frames, b.duration, b.speech_end) == (71, 60, 2.0, None)
    assert b.start == 2.367 and tl.total_frames == 131 and tl.total == 4.367


def test_later_scenes_start_on_a_frame_boundary_and_cues_are_absolute():
    m = parse([{"id": "a", "text": "一。"}, {"id": "b", "text": "二。"}])
    tl = timeline.build(m, clips_for(m, [1.0, 1.0]))
    a, b = tl.scenes
    # a：0.15+1.0+0.45 = 1.6 秒 = 48 幀，剛好；b 從第 48 幀（1.6 秒）開始
    assert (a.frames, b.start_frame, b.start) == (48, 48, 1.6)
    assert (b.cues[0].start, b.cues[0].end) == (1.75, 2.75)
    assert [c.index for c in tl.cues] == [0, 1]


def test_exact_frame_counts_do_not_gain_a_spare_frame():
    m = parse([{"id": "a", "min_duration": 2.0}, {"id": "b", "min_duration": 0.1}])
    tl = timeline.build(m, [])
    assert [s.frames for s in tl.scenes] == [60, 3]


def test_min_duration_wins_when_longer_than_narration_and_loses_when_shorter():
    m = parse([{"id": "a", "text": "短。", "min_duration": 5.0},
               {"id": "b", "text": "長。", "min_duration": 0.5}])
    tl = timeline.build(m, clips_for(m, [1.0, 3.0]))
    assert tl.scenes[0].duration == 5.0                 # 旁白只要 1.6 秒，撐到 5 秒
    assert tl.scenes[1].duration == 3.6                 # 旁白要 3.6 秒，不被 0.5 截斷


def test_custom_timing_and_fps():
    m = parse([{"id": "a", "text": "一。"}], timing={"lead": 0, "gap": 0, "tail": 0},
              output={"fps": 25})
    tl = timeline.build(m, clips_for(m, [1.0]))
    assert tl.fps == 25 and tl.scenes[0].frames == 25
    assert (tl.cues[0].start, tl.cues[0].end) == (0.0, 1.0)


def test_english_text_travels_with_the_cue():
    m = parse([{"id": "a", "sentences": [{"text": "夜市。", "en": "Night market."}]}])
    cue = timeline.build(m, clips_for(m, [1.0])).cues[0]
    assert (cue.text, cue.en, cue.scene_id) == ("夜市。", "Night market.", "a")


def test_clips_must_match_the_manifest_sentences():
    m = parse([{"id": "a", "text": "一。二。"}])
    with pytest.raises(ValueError):
        timeline.build(m, clips_for(m, [1.0, 1.0])[:1])
    wrong = clips_for(m, [1.0, 1.0])[::-1]
    with pytest.raises(ValueError):
        timeline.build(m, wrong)


def test_length_warnings():
    def warnings(*scene_seconds):
        m = parse([{"id": f"s{i}", "min_duration": sec} for i, sec in enumerate(scene_seconds)])
        return timeline.build(m, []).warnings

    assert warnings(59.0) == ()
    assert "超過 60 秒" in warnings(75.0)[0]
    over = warnings(100.0, 81.0)
    assert len(over) == 1 and "3 分鐘" in over[0] and "不會被當成 Shorts" in over[0]


def test_to_dict_is_json_with_stable_keys_and_clip_file_names_only():
    m = parse([{"id": "a", "text": "一。"}])
    data = timeline.build(m, clips_for(m, [1.0], directory="/some/where")).to_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data
    assert data["schema"] == "vidpipe.timeline/1" and data["short_id"] == "t1"
    cue = data["scenes"][0]["sentences"][0]
    assert cue["clip"] == "0.wav" and "/" not in cue["clip"]
    assert set(data["scenes"][0]) == {"id", "index", "start_frame", "frames", "start", "end",
                                      "duration", "speech_end", "sentences"}


# ------------------------------------------------------------------ 旁白音軌


def samples(path):
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        return [int.from_bytes(raw[i:i + 2], "little", signed=True)
                for i in range(0, len(raw), 2 * w.getnchannels())], w.getframerate()


def test_narration_wav_places_each_sentence_at_its_start_with_silence_between(tmp_path):
    m = parse([{"id": "a", "sentences": [{"text": "一。"}, {"text": "二。"}]},
               {"id": "b", "min_duration": 1.0}])
    one, two = tone(tmp_path / "1.wav", 1.0, 1000), tone(tmp_path / "2.wav", 0.5, 2000)
    clips = [Clip(0, "一。", one, 1.0, False), Clip(1, "二。", two, 0.5, False)]
    tl = timeline.build(m, clips)
    info = timeline.write_narration(tl, tmp_path / "out" / "narration.wav")
    data, rate = samples(info["path"])
    assert rate == RATE and info["sample_rate"] == RATE and info["channels"] == 1
    assert len(data) == round(tl.total * RATE) and info["duration"] == tl.total

    def at(seconds):
        return data[int(seconds * RATE)]

    assert at(0.05) == 0 and at(0.14) == 0                   # lead
    assert at(0.2) == 1000 and at(1.1) == 1000               # 第一句 0.15–1.15
    assert at(1.2) == 0 and at(1.35) == 0                    # gap
    assert at(1.5) == 2000 and at(1.85) == 2000              # 第二句 1.40–1.90
    assert at(2.0) == 0 and at(tl.total - 0.01) == 0         # tail 與沒有旁白的場景
    assert not list((tmp_path / "out").glob(".*"))           # 沒留暫存檔


def test_the_same_clip_can_appear_twice(tmp_path):
    m = parse([{"id": "a", "sentences": [{"text": "好。"}, {"text": "好。"}]}])
    wav = tone(tmp_path / "x.wav", 0.5)
    clips = [Clip(0, "好。", wav, 0.5, False), Clip(1, "好。", wav, 0.5, True)]
    info = timeline.write_narration(timeline.build(m, clips), tmp_path / "n.wav")
    data, _ = samples(info["path"])
    assert data[int(0.3 * RATE)] == 1000 and data[int(1.2 * RATE)] == 1000


def test_write_narration_refuses_a_timeline_without_sentences(tmp_path):
    tl = timeline.build(parse([{"id": "a", "min_duration": 1}]), [])
    with pytest.raises(BadInput):
        timeline.write_narration(tl, tmp_path / "n.wav")


@needs_ffmpeg
def test_clips_in_another_format_are_converted_to_the_first_clips_format(tmp_path):
    m = parse([{"id": "a", "sentences": [{"text": "一。"}, {"text": "二。"}]}])
    clips = [Clip(0, "一。", tone(tmp_path / "1.wav", 1.0, rate=RATE), 1.0, False),
             Clip(1, "二。", tone(tmp_path / "2.wav", 1.0, rate=16000, channels=2), 1.0, False)]
    tl = timeline.build(m, clips)
    info = timeline.write_narration(tl, tmp_path / "n.wav")
    data, rate = samples(info["path"])
    assert rate == RATE and info["channels"] == 1
    assert abs(len(data) / RATE - tl.total) < 0.01
    assert data[int(1.8 * RATE)] > 500                         # 第二句（1.4–2.4）有聲音


# ------------------------------------------------------------------ 一次做完（要 TTS 與 ffmpeg）

MANIFEST = {
    "schema": "vidpipe.manifest/1", "id": "night-01", "title": "夜市",
    "voice": {"model": "cosy-test"},
    "scenes": [{"id": "s1", "text": "你好。晚安。"},
               {"id": "s2", "sentences": [{"text": "夜市熱鬧。", "en": "Lively."}]},
               {"id": "s3", "min_duration": 1.0}],
}


def _setup(tmp_path, fake_server, monkeypatch, data=None):
    FakeTTS(fake_server, speech=lambda text: 0.1 * len(text))
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    path = tmp_path / "shorts.json"
    path.write_text(json.dumps(data or MANIFEST, ensure_ascii=False), encoding="utf-8")
    return path


@needs_ffmpeg
def test_cli_narrate_writes_manifest_timeline_and_narration_into_the_short_folder(
        tmp_path, fake_server, monkeypatch, capsys):
    path = _setup(tmp_path, fake_server, monkeypatch)
    assert cli.main(["narrate", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    result = out["output"]
    root = tmp_path / "work" / "shorts" / "night-01"
    assert result["dir"] == str(root) and result["tts"] == {"sentences": 3, "synthesized": 3}
    assert sorted(p.name for p in root.iterdir()) == ["manifest.json", "narration.wav",
                                                      "timeline.json"]
    assert json.loads((root / "manifest.json").read_text(encoding="utf-8")) == MANIFEST
    tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
    assert tl["voice"]["model"] == "cosy-test"
    assert tl["narration"] == {"file": "narration.wav", "duration": tl["total"],
                               "sample_rate": RATE, "channels": 1}
    s1, s2, s3 = tl["scenes"]
    # 每句聲音 = 0.1 秒 × 字數 + 頭尾各留的 0.05 秒
    first = s1["sentences"][0]
    assert abs((first["end"] - first["start"]) - 0.4) < 0.04
    assert s2["sentences"][0]["en"] == "Lively." and s3["duration"] == 1.0
    assert tl["total"] == pytest.approx(s1["duration"] + s2["duration"] + s3["duration"], abs=0.01)
    with wave.open(str(root / "narration.wav"), "rb") as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(tl["total"], abs=0.01)
    state = jobs.get_job(tmp_path / "work", out["job"])
    assert state["kind"] == "narrate" and state["short_id"] == "night-01"
    assert [s["name"] for s in state["stages"]] == ["narrate", "timeline"]
    assert state["result"]["short_id"] == "night-01" and state["status"] == "succeeded"


@needs_ffmpeg
def test_rerunning_the_same_manifest_is_cached_and_produces_identical_files(
        tmp_path, fake_server, monkeypatch, capsys):
    path = _setup(tmp_path, fake_server, monkeypatch)
    root = tmp_path / "work" / "shorts" / "night-01"
    assert cli.main(["narrate", str(path)]) == 0
    first = {n: (root / n).read_bytes() for n in ("timeline.json", "narration.wav")}
    capsys.readouterr()
    assert cli.main(["narrate", str(path)]) == 0
    again = json.loads(capsys.readouterr().out)["output"]
    assert again["tts"] == {"sentences": 3, "synthesized": 0}
    assert {n: (root / n).read_bytes() for n in first} == first


@needs_ffmpeg
def test_manifest_voice_reaches_the_tts_request(tmp_path, fake_server, monkeypatch, capsys):
    path = _setup(tmp_path, fake_server, monkeypatch)
    monkeypatch.setenv("VIDPIPE_TTS_MODEL", "from-env")
    assert cli.main(["narrate", str(path)]) == 0
    capsys.readouterr()
    bodies = [json.loads(body) for _, p, _, body in fake_server.calls if p.endswith("/speech")]
    assert bodies and {b["model"] for b in bodies} == {"cosy-test"}  # manifest 蓋過環境變數


@needs_ffmpeg
def test_a_silent_manifest_gets_a_timeline_and_no_narration_track(
        tmp_path, fake_server, monkeypatch, capsys):
    data = {"schema": "vidpipe.manifest/1", "id": "quiet",
            "scenes": [{"id": "a", "min_duration": 1.5}, {"id": "b", "min_duration": 2.0}]}
    path = _setup(tmp_path, fake_server, monkeypatch, data)
    root = tmp_path / "work" / "shorts" / "quiet"
    root.mkdir(parents=True)
    (root / "narration.wav").write_bytes(b"stale")  # 上一版留下的
    assert cli.main(["narrate", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)["output"]
    assert result["narration"] is None and result["total"] == 3.5
    assert not (root / "narration.wav").exists()
    assert json.loads((root / "timeline.json").read_text(encoding="utf-8"))["narration"] is None
    assert not [c for c in fake_server.calls if c[1].endswith("/speech")]


@needs_ffmpeg
def test_a_failed_tts_leaves_no_half_written_timeline_and_marks_the_job_failed(
        tmp_path, fake_server, monkeypatch, capsys):
    path = _setup(tmp_path, fake_server, monkeypatch)
    FakeTTS(fake_server, fail_on={"夜市熱鬧。"})
    assert cli.main(["narrate", str(path)]) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "upstream_error"
    root = tmp_path / "work" / "shorts" / "night-01"
    assert not root.exists() or not any(root.iterdir())
    listing = jobs.list_jobs(tmp_path / "work")
    assert listing[0]["status"] == "failed" and listing[0]["short_id"] == "night-01"


def test_cli_validate_checks_without_touching_tts_and_estimates_length(
        tmp_path, fake_server, monkeypatch, capsys):
    path = _setup(tmp_path, fake_server, monkeypatch)
    assert cli.main(["validate", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["short_id"] == "night-01" and fake_server.calls == []
    s1, s2, s3 = out["scenes"]
    assert s1["sentences"] == ["你好。", "晚安。"] and s1["chars"] == 6
    assert s3["estimated_seconds"] == 1.0 and "粗估" in out["estimate_note"]
    assert out["estimated_seconds"] == pytest.approx(
        sum(s["estimated_seconds"] for s in out["scenes"]), abs=0.11)
    assert not (tmp_path / "work").exists()  # validate 不留下任何東西


def test_cli_validate_reports_the_path_of_a_bad_field_with_exit_code_2(
        tmp_path, fake_server, monkeypatch, capsys):
    bad = json.loads(json.dumps(MANIFEST))
    bad["scenes"][1]["txt"] = "拼錯"
    path = _setup(tmp_path, fake_server, monkeypatch, bad)
    assert cli.main(["validate", str(path)]) == 2
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "bad_input" and "scenes[1]" in err["message"] and "txt" in err["message"]
    assert cli.main(["narrate", str(tmp_path / "missing.json")]) == 1  # NotFound
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not_found"


def test_short_dir_never_collides_with_work_dir_internals(tmp_path):
    cfg = Config(work_dir=str(tmp_path / "work"))
    # 作品 id 叫 cache 也行：作品都在 shorts/ 底下，不會蓋到 work/cache 或 work/_jobs
    assert timeline.short_dir(cfg, "cache") == tmp_path / "work" / "shorts" / "cache"
