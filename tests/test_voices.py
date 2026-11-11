"""參考音檔資料夾：掃描、用 ASR 補逐字稿、決定實際送給 TTS 的音色。"""
from __future__ import annotations

import json

import pytest

from vidpipe import cli, voices
from vidpipe.config import Config
from vidpipe.errors import BadInput, NotFound


@pytest.fixture
def refdir(tmp_path):
    d = tmp_path / "ref"
    d.mkdir()
    (d / "a.wav").write_bytes(b"RIFF")
    (d / "b.wav").write_bytes(b"RIFF")
    (d / "b.txt").write_text("已經有的逐字稿。\n", encoding="utf-8")
    (d / "notes.md").write_text("不是音檔", encoding="utf-8")
    return d


class FakeASR:
    def __init__(self, text="有了它,在外面再也不怕被蚊子咬了。", fail=()):
        self.calls, self.text, self.fail = [], text, set(fail)

    def transcribe(self, path, *, model, language=""):
        from vidpipe.errors import UpstreamError

        self.calls.append((path, model, language))
        if path.endswith(tuple(self.fail)):
            raise UpstreamError("unknown model id", upstream="tts")
        return self.text


def test_scan_lists_audio_files_with_their_transcripts(refdir):
    items = voices.scan(Config(voice_ref_dir=str(refdir)))
    assert [i["name"] for i in items] == ["a.wav", "b.wav"]
    assert (items[0]["has_transcript"], items[1]["text"]) == (False, "已經有的逐字稿。")


def test_missing_transcripts_are_recognised_and_saved_next_to_the_audio(refdir):
    asr = FakeASR()
    cfg = Config(voice_ref_dir=str(refdir), asr_model="qwen3-asr", asr_language="zh")
    out = voices.transcribe_missing(cfg, client=asr)
    assert asr.calls == [(str(refdir / "a.wav"), "qwen3-asr", "zh")]      # 已有的不重做
    assert (out["transcribed"], out["existing"], out["failed"]) == (1, 1, 0)
    assert (refdir / "a.txt").read_text(encoding="utf-8") == "有了它，在外面再也不怕被蚊子咬了。\n"
    assert voices.transcribe_missing(cfg, client=asr)["transcribed"] == 0    # 再跑一次什麼都不做


def test_one_failure_does_not_stop_the_others(refdir):
    (refdir / "c.wav").write_bytes(b"RIFF")
    cfg = Config(voice_ref_dir=str(refdir), asr_model="m")
    out = voices.transcribe_missing(cfg, client=FakeASR(fail=("a.wav",)))
    assert (out["transcribed"], out["failed"]) == (1, 1)
    assert not (refdir / "a.txt").exists() and (refdir / "c.txt").is_file()


def test_needs_a_folder_and_an_asr_model(tmp_path, refdir):
    with pytest.raises(BadInput):
        voices.scan(Config())
    with pytest.raises(NotFound):
        voices.scan(Config(voice_ref_dir=str(tmp_path / "nope")))
    with pytest.raises(BadInput) as err:
        voices.transcribe_missing(Config(voice_ref_dir=str(refdir)), client=FakeASR())
    assert "asr_model" in err.value.message


def test_tidy_turns_simplified_into_taiwan_traditional_when_opencc_is_installed():
    pytest.importorskip("opencc")
    assert voices.tidy("你看这只母蚊子,飞了几十米") == "你看這隻母蚊子，飛了幾十米"


def test_resolve_finds_a_bare_file_name_in_the_folder_and_reads_its_transcript(refdir):
    cfg = voices.resolve(Config(voice_ref_dir=str(refdir), voice_ref="b.wav"))
    assert cfg.voice_ref == str(refdir / "b.wav") and cfg.reference_text == "已經有的逐字稿。"
    kept = voices.resolve(Config(voice_ref=str(refdir / "b.wav"), reference_text="自己填的"))
    assert kept.reference_text == "自己填的"                                 # 填了就用填的
    assert voices.resolve(Config()) == Config()


def test_cli_lists_and_transcribes(refdir, monkeypatch, capsys):
    monkeypatch.setenv("VIDPIPE_VOICE_REF_DIR", str(refdir))
    monkeypatch.setenv("VIDPIPE_ASR_MODEL", "m")
    monkeypatch.setenv("VIDPIPE_RECORD_JOBS", "false")
    monkeypatch.setattr(voices, "make_client", lambda cfg: FakeASR(text="辨識結果。"))
    assert cli.main(["voices"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [i["has_transcript"] for i in listed["items"]] == [False, True]
    assert cli.main(["voices", "--transcribe"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["ok"] and done["output"]["transcribed"] == 1
    assert (refdir / "a.txt").read_text(encoding="utf-8").strip() == "辨識結果。"
