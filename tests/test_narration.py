from __future__ import annotations

import io
import json
import math
import shutil
import struct
import threading
import time
import wave

import pytest

from vidpipe import cli, jobs, narration
from vidpipe.config import Config
from vidpipe.errors import BadInput, UpstreamError

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

RATE = 24000


def make_wav(lead=0.3, speech=0.5, tail=0.4, freq=440.0) -> bytes:
    """前後各有一段靜音、中間是音調的 WAV（24kHz 單聲道 16-bit，跟 audio.cpp 一樣）。"""
    frames = bytearray()
    frames += b"\x00\x00" * int(RATE * lead)
    for n in range(int(RATE * speech)):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * n / RATE)))
    frames += b"\x00\x00" * int(RATE * tail)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class FakeTTS:
    """記錄每次請求；可以指定哪些句子要失敗、或回靜音。"""

    def __init__(self, fake_server, *, fail_on=(), silent_on=(), delay=0.0):
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self.fail_on, self.silent_on, self.delay = set(fail_on), set(silent_on), delay
        fake_server.handler = self.handle

    def handle(self, method, path, headers, body):
        if path != "/v1/audio/speech":
            return 404, {"error": "no route"}
        req = json.loads(body)
        with self.lock:
            self.calls.append(req)
        if self.delay:
            time.sleep(self.delay)
        if req["input"] in self.fail_on:
            return 500, {"error": {"message": "boom", "code": "tts_failed"}}
        if req["input"] in self.silent_on:
            return 200, make_wav(lead=0.5, speech=0.0, tail=0.5)
        return 200, make_wav()

    @property
    def texts(self):
        return [c["input"] for c in self.calls]


def _cfg(tmp_path, fake_server, **kw):
    return Config(work_dir=str(tmp_path / "work"), out_dir=str(tmp_path / "out"),
                  tts_base_url=fake_server.url, **kw)


def _client_factory(fake_server):
    from vidpipe.clients import TTSClient

    def make(cfg):
        return TTSClient(cfg.tts_base_url, cfg.tts_token, model=cfg.tts_model,
                         voice_ref=cfg.voice_ref, reference_text=cfg.reference_text, retries=0)
    return make


def run(sentences, cfg, fake_server, **kw):
    return narration.synthesize(sentences, cfg, make_client=_client_factory(fake_server), **kw)


def test_leading_and_trailing_silence_is_trimmed_but_not_the_speech(tmp_path, fake_server):
    FakeTTS(fake_server)
    clips = run(["你好。"], _cfg(tmp_path, fake_server), fake_server)
    # 0.5 秒的聲音 + 頭尾各留 0.05 秒；原本是 1.2 秒
    assert 0.55 <= clips[0].duration <= 0.68, clips[0].duration
    with wave.open(clips[0].path, "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2)


def test_results_keep_input_order_and_identical_sentences_are_synthesized_once(
        tmp_path, fake_server):
    tts = FakeTTS(fake_server)
    clips = run(["甲。", "乙。", "甲。", "丙。"], _cfg(tmp_path, fake_server), fake_server)
    assert [c.text for c in clips] == ["甲。", "乙。", "甲。", "丙。"]
    assert [c.index for c in clips] == [0, 1, 2, 3]
    assert clips[0].path == clips[2].path and clips[0].path != clips[1].path
    assert sorted(tts.texts) == ["丙。", "乙。", "甲。"]


def test_second_run_is_served_from_cache_without_any_request(tmp_path, fake_server):
    tts = FakeTTS(fake_server)
    cfg = _cfg(tmp_path, fake_server)
    first = run(["甲。", "乙。"], cfg, fake_server)
    assert [c.cached for c in first] == [False, False] and len(tts.calls) == 2
    second = run(["甲。", "乙。"], cfg, fake_server)
    assert [c.cached for c in second] == [True, True] and len(tts.calls) == 2
    assert [c.duration for c in second] == [c.duration for c in first]
    # 只改一句 → 只重做那一句
    third = run(["甲。", "乙乙。"], cfg, fake_server)
    assert [c.cached for c in third] == [True, False] and len(tts.calls) == 3


def test_changing_model_voice_or_reference_text_invalidates_the_cache(tmp_path, fake_server):
    tts = FakeTTS(fake_server)
    base = _cfg(tmp_path, fake_server, tts_model="cosy", voice_ref="/v/a.wav", reference_text="甲")
    run(["你好。"], base, fake_server)
    for change in ({"tts_model": "other"}, {"voice_ref": "/v/b.wav"}, {"reference_text": "乙"}):
        before = len(tts.calls)
        cfg = _cfg(tmp_path, fake_server, **{**{"tts_model": "cosy", "voice_ref": "/v/a.wav",
                                                 "reference_text": "甲"}, **change})
        run(["你好。"], cfg, fake_server)
        assert len(tts.calls) == before + 1, change
    # 換服務位址（同一個模型與音色）不該重做
    other_url = _cfg(tmp_path, fake_server, tts_model="cosy", voice_ref="/v/a.wav",
                     reference_text="甲")
    other_url.tts_base_url = fake_server.url + "/"
    before = len(tts.calls)
    run(["你好。"], other_url, fake_server)
    assert len(tts.calls) == before
    # 請求真的帶了模型與音色
    assert tts.calls[0]["model"] == "cosy" and tts.calls[0]["voice_ref"] == "/v/a.wav"


def test_concurrency_runs_requests_in_parallel_but_keeps_order(tmp_path, fake_server):
    tts = FakeTTS(fake_server, delay=0.4)
    sentences = [f"第{i}句。" for i in range(4)]
    started = time.time()
    clips = run(sentences, _cfg(tmp_path, fake_server), fake_server, concurrency=4)
    parallel = time.time() - started
    assert [c.text for c in clips] == sentences and len(tts.calls) == 4
    assert parallel < 4 * 0.4 * 0.75, parallel  # 序列要 1.6 秒以上


def test_failure_stops_the_run_and_keeps_finished_sentences_cached(tmp_path, fake_server):
    tts = FakeTTS(fake_server, fail_on={"壞句。"})
    cfg = _cfg(tmp_path, fake_server)
    with pytest.raises(UpstreamError) as err:
        run(["好句。", "壞句。", "後句。"], cfg, fake_server, concurrency=1)
    assert err.value.upstream == "tts"
    root = narration.cache_dir(cfg)
    names = sorted(p.name for p in root.iterdir())
    assert all(not n.startswith(".") for n in names), names  # 沒有殘留的暫存檔
    assert narration.cache_key("好句。", cfg) + ".wav" in names
    assert "後句。" not in tts.texts  # 失敗後不再送出尚未開始的句子
    # 修好之後重跑，只補缺的
    tts.fail_on.clear()
    before = len(tts.calls)
    clips = run(["好句。", "壞句。", "後句。"], cfg, fake_server, concurrency=1)
    assert len(tts.calls) - before == 2 and [c.cached for c in clips] == [True, False, False]


def test_silent_audio_is_reported_not_cached(tmp_path, fake_server):
    FakeTTS(fake_server, silent_on={"。"})
    cfg = _cfg(tmp_path, fake_server)
    with pytest.raises(UpstreamError, match="靜音") as err:
        run(["。"], cfg, fake_server)
    assert err.value.retryable is True
    assert list(narration.cache_dir(cfg).iterdir()) == []


def test_non_wav_reply_is_an_upstream_error(tmp_path, fake_server):
    fake_server.handler = lambda m, p, h, b: (200, b"ID3 not a wav")
    with pytest.raises(UpstreamError, match="WAV"):
        run(["你好。"], _cfg(tmp_path, fake_server), fake_server)


def test_empty_input_is_rejected(tmp_path, fake_server):
    FakeTTS(fake_server)
    cfg = _cfg(tmp_path, fake_server)
    for bad in ([], ["好。", "  "]):
        with pytest.raises(BadInput):
            run(bad, cfg, fake_server)


def test_progress_and_stage_are_reported_to_the_active_job(tmp_path, fake_server):
    FakeTTS(fake_server)
    cfg = _cfg(tmp_path, fake_server)
    with jobs.JobRecorder(cfg.work_dir, "narrate") as job:
        run(["甲。", "乙。", "丙。"], cfg, fake_server)
    state = jobs.get_job(cfg.work_dir, job.id)
    assert [s["name"] for s in state["stages"]] == ["narrate"]
    assert "3 句要合成" in state["stages"][0]["detail"]
    logs = [e["message"] for e in jobs.read_events(cfg.work_dir, job.id) if e["kind"] == "log"]
    assert len(logs) == 3


def test_cache_key_depends_on_text_and_voice_settings_only():
    a = narration.cache_key("你好。", Config(tts_model="m"))
    assert a == narration.cache_key("你好。", Config(tts_model="m", tts_base_url="http://elsewhere"))
    assert a != narration.cache_key("你好", Config(tts_model="m"))
    assert a != narration.cache_key("你好。", Config(tts_model="n"))


def test_wav_duration_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFFxxxxnope")
    with pytest.raises(UpstreamError):
        narration.wav_duration(bad)


def test_cli_speak_writes_job_clips_and_numbered_exports(tmp_path, fake_server, capsys,
                                                         monkeypatch):
    FakeTTS(fake_server)
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    out_dir = tmp_path / "wavs"
    assert cli.main(["speak", "--text", "第一句。第二句。", "--out-dir", str(out_dir)]) == 0
    data = json.loads(capsys.readouterr().out)
    out = data["output"]
    assert out["count"] == 2 and out["cached"] == 0 and 1.0 < out["total_duration"] < 1.4
    assert sorted(p.name for p in out_dir.iterdir()) == ["001.wav", "002.wav"]
    state = jobs.get_job(tmp_path / "work", data["job"])
    assert state["kind"] == "narrate" and state["result"]["count"] == 2
    # 再跑一次：全部走快取
    assert cli.main(["speak", "--text", "第一句。第二句。"]) == 0
    assert json.loads(capsys.readouterr().out)["output"]["cached"] == 2


def test_config_tts_concurrency_validation(monkeypatch):
    from vidpipe import config as config_mod
    from vidpipe.errors import ConfigError

    monkeypatch.setenv("VIDPIPE_TTS_CONCURRENCY", "3")
    assert config_mod.load().tts_concurrency == 3
    for bad in ("0", "99", "many"):
        monkeypatch.setenv("VIDPIPE_TTS_CONCURRENCY", bad)
        with pytest.raises(ConfigError):
            config_mod.load()


def test_sigterm_during_synthesis_cancels_the_job_and_stops_new_requests(tmp_path, fake_server):
    import os
    import signal

    tts = FakeTTS(fake_server, delay=0.5)
    cfg = _cfg(tmp_path, fake_server)
    sentences = [f"第{i}句。" for i in range(6)]
    timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with pytest.raises(jobs.Cancelled):
            with jobs.JobRecorder(cfg.work_dir, "narrate") as job:
                run(sentences, cfg, fake_server, concurrency=1)
    finally:
        timer.cancel()
    assert jobs.get_job(cfg.work_dir, job.id)["status"] == "cancelled"
    time.sleep(1.2)  # 讓已經在送的那一句收尾
    assert len(tts.calls) <= 2, tts.texts  # 取消之後不再送出新的句子
    leftovers = [p.name for p in narration.cache_dir(cfg).iterdir() if p.name.startswith(".")]
    assert leftovers == []
