from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from vidpipe import cli, ffmpeg, video
from vidpipe.errors import BadInput, NotFound
from vidpipe.video import OutputSpec

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")


def _make(path, lavfi: str, *, audio: bool, seconds: float = 2.0, fps: int = 25):
    """用合成來源做測試影片。lavfi 是影像來源（含尺寸）。"""
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{lavfi}:d={seconds}:r={fps}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=d={seconds}"]
    args += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    subprocess.run([*args, str(path)], check=True)
    return path


def _pixel(path, x: int, y: int) -> tuple[int, int, int]:
    """取第一格的某個像素（RGB）。"""
    info = ffmpeg.probe(path)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    off = (y * info["width"] + x) * 3
    return raw[off], raw[off + 1], raw[off + 2]


# 左半紅、右半藍的橫式影片：用來驗證 crop 的 focus 裁到哪一邊
def _red_blue(path):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=320x360:d=1:r=25",
            "-f", "lavfi", "-i", "color=blue:s=320x360:d=1:r=25",
            "-filter_complex", "[0:v][1:v]hstack", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


def test_spec_validation():
    with pytest.raises(BadInput, match="偶數"):
        OutputSpec(width=1081, height=1920)
    with pytest.raises(BadInput, match="fps"):
        OutputSpec(fps=0)
    assert OutputSpec.for_aspect("1:1").height == 1080
    with pytest.raises(BadInput, match="長寬比"):
        OutputSpec.for_aspect("4:3")


def test_parse_focus():
    assert video.parse_focus(None) == (0.5, 0.5)
    assert video.parse_focus("0.2,0.8") == (0.2, 0.8)
    assert video.parse_focus((0, 1)) == (0.0, 1.0)
    for bad in ("1,2,3", "a,b", "1.5,0.5", "-1,0"):
        with pytest.raises(BadInput):
            video.parse_focus(bad)


def test_video_filter_is_plain_scale_when_aspect_already_matches():
    spec = OutputSpec()
    for fit in video.FITS:
        graph = video.video_filter(540, 960, spec, fit)
        assert "crop" not in graph and "pad" not in graph and "split" not in graph
    assert "crop=1080:1920" in video.video_filter(1920, 1080, spec, "crop")
    assert "overlay" in video.video_filter(1920, 1080, spec, "blur_pad")
    assert "pad=1080:1920" in video.video_filter(1920, 1080, spec, "letterbox")
    with pytest.raises(BadInput):
        video.video_filter(1920, 1080, spec, "stretch")


@pytest.mark.parametrize("fit", video.FITS)
def test_landscape_to_vertical_unified_spec(tmp_path, fit):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=True, fps=25)
    out = video.normalize(src, tmp_path / "out.mp4", fit=fit)
    assert (out["width"], out["height"]) == (1080, 1920)
    assert out["fps"] == 30.0 and out["vcodec"] == "h264" and out["pix_fmt"] == "yuv420p"
    assert out["acodec"] == "aac" and out["sample_rate"] == 48000 and out["channels"] == 2
    assert abs(out["duration"] - 2.0) < 0.15
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []  # 沒留殘檔


def test_portrait_source_is_just_scaled(tmp_path):
    src = _make(tmp_path / "in.mp4", "testsrc=s=360x640", audio=False)
    out = video.normalize(src, tmp_path / "out.mp4", fit="crop")
    assert (out["width"], out["height"]) == (1080, 1920)


def test_no_audio_source_gets_silent_track_so_clips_stay_uniform(tmp_path):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=False)
    out = video.normalize(src, tmp_path / "out.mp4", audio="keep")
    assert out["has_audio"] is True and out["acodec"] == "aac"
    assert abs(out["duration"] - 2.0) < 0.15


def test_silent_mode_replaces_original_audio(tmp_path):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=True)
    out = video.normalize(src, tmp_path / "out.mp4", audio="silent")
    cmd = ["ffmpeg", "-v", "info", "-i", out["path"],
           "-af", "volumedetect", "-vn", "-f", "null", "-"]
    vol = subprocess.run(cmd, capture_output=True, text=True, check=True).stderr
    assert "max_volume: -91" in vol or "mean_volume: -91" in vol


def test_crop_focus_picks_which_side_is_kept(tmp_path):
    src = _red_blue(tmp_path / "rb.mp4")
    left = video.normalize(src, tmp_path / "l.mp4", fit="crop", focus="0,0.5", audio="silent")
    right = video.normalize(src, tmp_path / "r.mp4", fit="crop", focus="1,0.5", audio="silent")
    cx, cy = 540, 960
    lr, lg, lb = _pixel(left["path"], cx, cy)
    rr, rg, rb = _pixel(right["path"], cx, cy)
    assert lr > 180 and lb < 80  # 保留左邊 → 紅
    assert rb > 180 and rr < 80  # 保留右邊 → 藍


def test_blur_pad_vs_letterbox_border(tmp_path):
    src = _make(tmp_path / "g.mp4", "color=green:s=640x360", audio=False)
    pad = video.normalize(src, tmp_path / "pad.mp4", fit="blur_pad", audio="silent")
    box = video.normalize(src, tmp_path / "box.mp4", fit="letterbox", audio="silent")
    top_pad = _pixel(pad["path"], 540, 10)
    top_box = _pixel(box["path"], 540, 10)
    assert top_pad[1] > 80 and top_pad[0] < 80   # 模糊底：上緣是綠的延伸
    assert max(top_box) < 40                     # 黑邊
    center = _pixel(pad["path"], 540, 960)
    assert center[1] > 120 and center[0] < 80    # 原片完整在中間


def test_other_aspects(tmp_path):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=False)
    sq = video.normalize(src, tmp_path / "sq.mp4", OutputSpec.for_aspect("1:1", fps=24))
    assert (sq["width"], sq["height"], sq["fps"]) == (1080, 1080, 24.0)
    wide = video.normalize(src, tmp_path / "wide.mp4", OutputSpec.for_aspect("16:9"))
    assert (wide["width"], wide["height"]) == (1920, 1080)


def test_variable_frame_rate_input_becomes_constant(tmp_path):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=False, fps=10)
    out = video.normalize(src, tmp_path / "out.mp4")
    assert out["fps"] == 30.0


def test_normalize_rejects_audio_only_and_missing_files(tmp_path):
    audio = tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1",
                    "-c:a", "aac", str(audio)], check=True)
    with pytest.raises(BadInput, match="沒有影像軌"):
        video.normalize(audio, tmp_path / "o.mp4")
    with pytest.raises(NotFound):
        video.normalize(tmp_path / "nope.mp4", tmp_path / "o.mp4")
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(ffmpeg.RenderError):
        video.normalize(bad, tmp_path / "o.mp4")


def test_bad_audio_mode(tmp_path):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=False)
    with pytest.raises(BadInput, match="audio"):
        video.normalize(src, tmp_path / "o.mp4", audio="loud")


def test_cli_normalize_and_probe(tmp_path, capsys):
    src = _make(tmp_path / "in.mp4", "testsrc=s=640x360", audio=True)
    out = tmp_path / "out" / "o.mp4"
    assert cli.main(["normalize", str(src), "-o", str(out), "--fit", "letterbox"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["output"]["height"] == 1920 and out.exists()
    assert cli.main(["probe", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["width"] == 1080
    assert cli.main(["probe", str(tmp_path / "missing.mp4")]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not_found"
