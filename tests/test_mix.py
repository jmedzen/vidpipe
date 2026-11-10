"""混音：旁白＋背景音樂＋響度標準化。用不同頻率的純音當旁白與音樂，才量得出各自的音量。"""

from __future__ import annotations

import math
import shutil

import pytest
from mediagen import peak, tones

from vidpipe import mix
from vidpipe.assets import Resolved
from vidpipe.config import Config
from vidpipe.errors import ConfigError
from vidpipe.video import OutputSpec

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

VOICE, MUSIC = 880.0, 220.0   # Hz（差兩個八度，帶通濾波才分得乾淨）
SPEC = OutputSpec()
TOTAL = 6.0


@pytest.fixture
def voice(tmp_path):
    """第 0.5–2.0 秒有人聲（880 Hz），其餘是靜音；整條 6 秒。"""
    return tones(tmp_path / "voice.wav", [(VOICE, 1.5)], amp=0.3, rate=24000, channels=1,
                 delay=0.5, total=TOTAL)


@pytest.fixture
def song(tmp_path):
    return tones(tmp_path / "song.wav", [(MUSIC, 10.0)], amp=0.25)


def settings(**music):
    return mix.music_settings({"path": "x", **music})


def build(tmp_path, narration, music_path, music_cfg, *, total=TOTAL, offset=0.0, **cfg):
    out = mix.plan(narration, Resolved(str(music_path), offset, {}) if music_path else None,
                   music_cfg, total, SPEC, Config(**cfg), tmp_path / "r")
    return out, tmp_path / "r" / "mix.wav"


# ------------------------------------------------------------------ 設定

def test_music_settings_fill_in_defaults_and_treat_zero_volume_as_off():
    assert mix.music_settings(None) is None and mix.music_settings({}) is None
    assert settings() == {"volume": 0.3, "duck": True, "start": 0.0}
    assert settings(duck_under_voice=False)["volume"] == 0.1      # 不壓低就要小聲一點
    assert settings(volume=0.5, start=3) == {"volume": 0.5, "duck": True, "start": 3.0}
    assert settings(volume=0) is None


def test_target_lufs_is_validated(monkeypatch):
    from vidpipe import config

    monkeypatch.setenv("VIDPIPE_TARGET_LUFS", "-16.5")
    assert config.load().target_lufs == -16.5
    for bad in ("0", "-40", "loud"):
        monkeypatch.setenv("VIDPIPE_TARGET_LUFS", bad)
        with pytest.raises(ConfigError):
            config.load()
    monkeypatch.setenv("VIDPIPE_TARGET_LUFS", "-14")
    monkeypatch.setenv("VIDPIPE_NORMALIZE_AUDIO", "false")
    assert config.load().normalize_audio is False


# ------------------------------------------------------------------ 壓低與音量

def test_music_is_ducked_while_the_narration_speaks_and_comes_back_in_the_gaps(
        tmp_path, voice, song):
    plan, mixed = build(tmp_path, voice, song, settings(volume=0.3), normalize_audio=False)
    assert plan.info["music"]["ducked"] is True
    under_voice = peak(mixed, 0.8, 1.0, band=MUSIC)
    in_gap = peak(mixed, 3.2, 1.0, band=MUSIC)
    assert in_gap > 3 * under_voice          # 壓低至少 9.5 dB
    assert peak(mixed, 0.8, 1.0, band=VOICE) > 3000   # 旁白本身沒被動到


def test_without_ducking_the_music_stays_put_under_the_voice(tmp_path, voice, song):
    plan, mixed = build(tmp_path, voice, song, settings(volume=0.3, duck_under_voice=False),
                        normalize_audio=False)
    assert plan.info["music"]["ducked"] is False
    assert peak(mixed, 0.8, 1.0, band=MUSIC) == pytest.approx(peak(mixed, 3.2, 1.0, band=MUSIC),
                                                              rel=0.1)


def test_volume_is_loudness_relative_to_the_narration_whatever_the_music_file_level(
        tmp_path, voice):
    """同一個 volume，不管音樂檔本來多大聲，音樂跟旁白的比例都一樣。"""
    ratios = []
    for amp in (0.05, 0.5):     # 差 20 dB 的兩個音樂檔
        track = tones(tmp_path / f"song{amp}.wav", [(MUSIC, 10.0)], amp=amp)
        _, mixed = build(tmp_path / str(amp), voice, track,
                         settings(volume=0.3, duck_under_voice=False), normalize_audio=False)
        ratios.append(peak(mixed, 0.8, 1.0, band=MUSIC) / peak(mixed, 0.8, 1.0, band=VOICE))
    assert ratios[0] == pytest.approx(ratios[1], rel=0.15)
    assert ratios[0] == pytest.approx(0.3, rel=0.3)    # 約是 volume 本身（K 加權在兩個頻率略有差）


def test_music_fades_in_and_out_and_is_cut_to_the_video_length(tmp_path, voice, song):
    _, mixed = build(tmp_path, voice, song, settings(volume=0.3, duck_under_voice=False),
                     normalize_audio=False)
    body = peak(mixed, 3.0, 1.0, band=MUSIC)
    assert peak(mixed, 0.0, 0.05, band=MUSIC) < body * 0.3     # 淡入
    assert peak(mixed, TOTAL - 0.15, 0.1, band=MUSIC) < body * 0.3   # 淡出
    from vidpipe import ffmpeg

    assert ffmpeg.probe(mixed)["duration"] == pytest.approx(TOTAL, abs=0.05)


# ------------------------------------------------------------------ 長度與起點

def test_music_shorter_than_the_video_loops_and_says_so(tmp_path):
    short = tones(tmp_path / "short.wav", [(MUSIC, 1.5)], amp=0.25)
    plan, mixed = build(tmp_path, None, short, settings(volume=1), normalize_audio=False)
    assert plan.info["music"]["looped"] is True
    assert len(plan.warnings) == 1 and "循環補長" in plan.warnings[0]
    assert peak(mixed, 3.5, 0.8, band=MUSIC) > 1000           # 第二、第三輪還在響
    assert plan.info["music"]["ducked"] is False               # 沒有旁白，沒有東西可以壓


def test_start_skips_the_intro_and_only_the_part_after_it_loops(tmp_path):
    song = tones(tmp_path / "two.wav", [(MUSIC, 2.0), (660.0, 2.0)], amp=0.25)
    plan, mixed = build(tmp_path, None, song, settings(volume=1, start=2.0), offset=2.0,
                        normalize_audio=False)
    assert plan.info["music"]["looped"] is True               # 起點之後只剩 2 秒，不夠 6 秒
    assert peak(mixed, 0.5, 1.0, band=660.0) > 1000
    assert peak(mixed, 0.5, 1.0, band=MUSIC) < 300            # 前奏被跳過
    assert peak(mixed, 3.0, 1.0, band=660.0) > 1000           # 循環的是起點之後那段
    assert peak(mixed, 3.0, 1.0, band=MUSIC) < 300


def test_a_start_past_the_end_of_the_music_is_reported_and_music_is_left_out(tmp_path, voice):
    short = tones(tmp_path / "short.wav", [(MUSIC, 1.5)], amp=0.25)
    plan, mixed = build(tmp_path, voice, short, settings(start=5), offset=5.0)
    assert plan.info["music"] is None and not mixed.exists()
    assert plan.path == voice and "沒有混入音樂" in plan.warnings[0]


# ------------------------------------------------------------------ 響度標準化

def loudness_after(plan):
    """把 plan 的濾鏡套上去之後的整合響度。"""
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(plan.path), "-af",
                        f"{plan.filter},aresample=48000", str(out)], check=True)
        return mix.measure(out)["i"]


@pytest.mark.parametrize("target", [-14.0, -20.0])
def test_the_result_is_normalized_to_the_target_loudness(tmp_path, voice, song, target):
    plan, _ = build(tmp_path, voice, song, settings(volume=0.3), target_lufs=target)
    assert plan.filter.startswith("loudnorm=") and "linear=true" in plan.filter
    assert plan.info["loudness"]["target"] == target
    assert loudness_after(plan) == pytest.approx(target, abs=1.0)


def test_narration_alone_is_normalized_too_without_building_a_mix(tmp_path, voice):
    plan, mixed = build(tmp_path, voice, None, None, target_lufs=-14.0)
    assert plan.path == voice and not mixed.exists()
    assert loudness_after(plan) == pytest.approx(-14.0, abs=1.0)


def test_normalization_can_be_switched_off(tmp_path, voice, song):
    plan, _ = build(tmp_path, voice, song, settings(), normalize_audio=False)
    assert plan.filter == "" and plan.info["loudness"] is None
    plain, _ = build(tmp_path / "x", voice, None, None, normalize_audio=False)
    assert plain.filter == "" and plain.path == voice


def test_nothing_to_play_means_no_audio_plan(tmp_path):
    plan, _ = build(tmp_path, None, None, None)
    assert plan.path is None and plan.filter == ""


def test_measuring_silence_gives_none_and_a_warning_instead_of_a_wild_gain(tmp_path):
    silent = tones(tmp_path / "silent.wav", [(VOICE, 1.0)], amp=0.0, rate=24000, channels=1,
                   total=TOTAL)
    assert mix.measure(silent) is None
    plan, _ = build(tmp_path, silent, None, None)
    assert plan.filter == "" and "量不出響度" in plan.warnings[0]
    assert math.isfinite(mix.GAIN_LIMITS[0])


def peaky_voice(path):
    """平均很小聲、但每半秒有一個很高的峰值：真實的 TTS 旁白就是這種樣子。"""
    import math
    import struct
    import wave

    rate, frames = 24000, []
    for n in range(rate * 6):
        t = n / rate
        amp = 0.95 if (t % 0.25) < 0.01 else 0.12
        frames.append(struct.pack("<h", int(32767 * amp * math.sin(2 * math.pi * 330 * t))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))
    return path


def test_peaky_narration_still_reaches_the_target_loudness(tmp_path):
    # loudnorm 的線性模式遇到峰值會少加增益，成片比目標小聲；要改用限幅器
    voice = peaky_voice(tmp_path / "peaky.wav")
    measured = mix.measure(voice)
    assert measured["tp"] + (-14.0 - measured["i"]) > mix.TRUE_PEAK      # 真的是峰值受限的情況
    plan, _ = build(tmp_path, voice, None, None, target_lufs=-14.0)
    assert plan.filter.startswith("volume=") and "alimiter=" in plan.filter
    assert loudness_after(plan) == pytest.approx(-14.0, abs=1.0)
    after = tmp_path / "after.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(voice), "-af",
                    f"{plan.filter},aresample=48000", str(after)], check=True)
    assert mix.measure(after)["tp"] <= -1.0                              # 峰值有擋住
