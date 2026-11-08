from __future__ import annotations

import json
import shutil

import pytest
from mediagen import color_clip, dominant, make, pixel

from vidpipe import cli, edit, ffmpeg, video
from vidpipe.errors import BadInput
from vidpipe.video import OutputSpec

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")


def _norm(tmp_path, name, colors, **kw):
    """做一支「每秒換一個顏色」的來源，再 normalize 成統一規格（小尺寸讓測試快）。"""
    raw = color_clip(tmp_path / f"{name}.raw.mp4", colors, **kw)
    out = video.normalize(raw, tmp_path / f"{name}.mp4", OutputSpec(width=360, height=640),
                          fit="letterbox")
    return out["path"]


def _color_at(path, t):
    info = ffmpeg.probe(path)
    return dominant(pixel(path, info["width"] // 2, info["height"] // 2, at=t))


def test_trim_is_frame_accurate_even_without_keyframes(tmp_path):
    # 紅 2 秒、藍 2 秒，關鍵幀只在 0 秒；用 copy 的話從 1.5 秒切會從 0 秒開始
    src = color_clip(tmp_path / "rb.mp4", ["red", "red", "blue", "blue"])
    out = edit.trim(src, tmp_path / "t.mp4", start=1.5, duration=1.0)
    assert abs(out["duration"] - 1.0) < 0.1
    assert _color_at(out["path"], 0.1) == "r"   # 來源 1.6s：紅
    assert _color_at(out["path"], 0.8) == "b"   # 來源 2.3s：藍
    late = edit.trim(src, tmp_path / "t2.mp4", start=2.5, duration=1.0)
    assert _color_at(late["path"], 0.0) == "b"


def test_trim_default_goes_to_end_and_keeps_fps(tmp_path):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=True, seconds=3, fps=24)
    out = edit.trim(src, tmp_path / "t.mp4", start=1.0)
    assert abs(out["duration"] - 2.0) < 0.1
    assert out["fps"] == 24.0 and out["has_audio"] is True
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_trim_rejects_bad_windows(tmp_path):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=False, seconds=2)
    with pytest.raises(edit.ClipTooShort) as short:
        edit.trim(src, tmp_path / "o.mp4", start=1.0, duration=3.0)
    assert short.value.code == "clip_too_short" and "fill" in short.value.hint
    for start in (-1.0, 2.0, 9.0):
        with pytest.raises(BadInput, match="start"):
            edit.trim(src, tmp_path / "o.mp4", start=start)
    with pytest.raises(BadInput, match="duration"):
        edit.trim(src, tmp_path / "o.mp4", start=0.0, duration=0.0)
    assert not (tmp_path / "o.mp4").exists()


def test_fill_trims_when_long_enough(tmp_path):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=True, seconds=3)
    out = edit.fill(src, tmp_path / "f.mp4", 1.5, start=0.5)
    assert out["looped"] is False and abs(out["duration"] - 1.5) < 0.1


def test_fill_loops_short_clip_to_exact_length(tmp_path):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=True, seconds=1)
    out = edit.fill(src, tmp_path / "f.mp4", 3.5)
    assert out["looped"] is True and abs(out["duration"] - 3.5) < 0.15
    assert out["has_audio"] is True


def test_fill_loops_only_the_part_after_start(tmp_path):
    # 紅 1 秒、藍 1 秒；從 1 秒起（藍）補成 3 秒：整段都該是藍，不能混進紅
    src = color_clip(tmp_path / "rb.mp4", ["red", "blue"])
    out = edit.fill(src, tmp_path / "f.mp4", 3.0, start=1.0)
    assert out["looped"] is True and abs(out["duration"] - 3.0) < 0.15
    for t in (0.2, 1.2, 2.2, 2.8):
        assert _color_at(out["path"], t) == "b", t
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_fill_without_loop_asks_caller_to_pick_another_clip(tmp_path):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=False, seconds=1)
    with pytest.raises(edit.ClipTooShort, match="不夠"):
        edit.fill(src, tmp_path / "f.mp4", 3.0, loop=False)
    assert not (tmp_path / "f.mp4").exists()


@pytest.mark.parametrize("copy", [False, True])
def test_concat_keeps_order_and_spec(tmp_path, copy):
    clips = [_norm(tmp_path, n, [c]) for n, c in (("a", "red"), ("b", "green"), ("c", "blue"))]
    out = edit.concat(clips, tmp_path / "all.mp4", copy=copy)
    assert abs(out["duration"] - 3.0) < 0.2
    assert (out["width"], out["height"], out["fps"]) == (360, 640, 30.0)
    assert out["vcodec"] == "h264" and out["acodec"] == "aac" and out["sample_rate"] == 48000
    assert [_color_at(out["path"], t) for t in (0.4, 1.5, 2.6)] == ["r", "g", "b"]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_concat_copy_handles_quotes_in_paths(tmp_path):
    odd = tmp_path / "it's here"
    odd.mkdir()
    a = _norm(odd, "a b", ["red"])
    b = _norm(tmp_path, "b", ["blue"])
    out = edit.concat([a, b], tmp_path / "all.mp4", copy=True)
    assert abs(out["duration"] - 2.0) < 0.2


def test_concat_single_input_and_empty(tmp_path):
    a = _norm(tmp_path, "a", ["red"])
    out = edit.concat([a], tmp_path / "one.mp4")
    assert abs(out["duration"] - 1.0) < 0.15
    with pytest.raises(BadInput, match="沒有要串接"):
        edit.concat([], tmp_path / "none.mp4")


def test_concat_names_the_clip_whose_spec_differs(tmp_path):
    good = _norm(tmp_path, "good", ["red"])
    odd = make(tmp_path / "odd.mp4", "testsrc=s=320x240", audio=True, seconds=1)
    with pytest.raises(BadInput) as err:
        edit.concat([good, odd], tmp_path / "x.mp4")
    assert "odd.mp4" in err.value.message and "規格不同" in err.value.message
    assert "normalize" in err.value.hint
    assert not (tmp_path / "x.mp4").exists()


def test_concat_rejects_clip_without_audio(tmp_path):
    good = _norm(tmp_path, "good", ["red"])
    mute = make(tmp_path / "mute.mp4", "testsrc=s=360x640", audio=False, seconds=1, fps=30)
    with pytest.raises(BadInput, match="沒有音軌"):
        edit.concat([good, mute], tmp_path / "x.mp4")


def test_concat_copy_requires_same_codecs(tmp_path):
    # 解析度與 fps 都一樣，但音訊不是 AAC：濾鏡模式可以重編碼，copy 不行
    good = _norm(tmp_path, "good", ["red"])
    other = tmp_path / "other.mp4"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", good, "-c:v", "copy", "-c:a", "libmp3lame",
                    "-ar", "48000", "-ac", "2", str(other)], check=True)
    assert ffmpeg.probe(other)["acodec"] == "mp3"
    with pytest.raises(BadInput, match="規格不同"):
        edit.concat([good, other], tmp_path / "x.mp4", copy=True)
    out = edit.concat([good, other], tmp_path / "y.mp4")
    assert out["acodec"] == "aac"


def test_cli_trim_fill_concat(tmp_path, capsys):
    a = _norm(tmp_path, "a", ["red", "red"])
    out = tmp_path / "o" / "t.mp4"
    assert cli.main(["trim", a, "-o", str(out), "--start", "0.5", "--duration", "1"]) == 0
    assert abs(json.loads(capsys.readouterr().out)["output"]["duration"] - 1.0) < 0.1

    filled = tmp_path / "f.mp4"
    assert cli.main(["fill", str(out), "-o", str(filled), "--duration", "2.5"]) == 0
    data = json.loads(capsys.readouterr().out)["output"]
    assert data["looped"] is True and abs(data["duration"] - 2.5) < 0.15

    assert cli.main(["fill", str(out), "-o", str(tmp_path / "n.mp4"), "--duration", "5",
                     "--no-loop"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "clip_too_short"

    both = tmp_path / "both.mp4"
    assert cli.main(["concat", str(out), str(filled), "-o", str(both)]) == 0
    assert abs(json.loads(capsys.readouterr().out)["output"]["duration"] - 3.5) < 0.2
    raw = make(tmp_path / "raw.mp4", "testsrc=s=320x240", audio=True, seconds=1)
    assert cli.main(["concat", str(out), str(raw), "-o", str(tmp_path / "bad.mp4")]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_input"
