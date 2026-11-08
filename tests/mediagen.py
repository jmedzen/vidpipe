"""測試用的合成影片：不靠任何外部素材，用 ffmpeg lavfi 現做。"""

from __future__ import annotations

import array
import shutil
import subprocess
from collections.abc import Sequence

from vidpipe import ffmpeg


def make(path, lavfi: str, *, audio: bool, seconds: float = 2.0, fps: int = 25):
    """用合成來源做測試影片。lavfi 是影像來源（含尺寸）。"""
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{lavfi}:d={seconds}:r={fps}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=d={seconds}"]
    args += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    subprocess.run([*args, str(path)], check=True)
    return path


def color_clip(path, colors: Sequence[str], *, seg: float = 1.0, size: str = "320x240",
               fps: int = 25, audio: bool = True):
    """依序每 seg 秒換一個顏色。關鍵幀只在開頭，這樣精準裁切才測得出來（copy 會切歪）。"""
    args = ["ffmpeg", "-v", "error", "-y"]
    for color in colors:
        args += ["-f", "lavfi", "-i", f"color={color}:s={size}:d={seg}:r={fps}"]
    total = seg * len(colors)
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=d={total}"]
    pads = "".join(f"[{n}:v]" for n in range(len(colors)))
    args += ["-filter_complex", f"{pads}concat=n={len(colors)}:v=1:a=0[v]", "-map", "[v]"]
    if audio:
        args += ["-map", f"{len(colors)}:a", "-c:a", "aac"]
    args += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
             "-g", "1000", "-keyint_min", "1000", "-sc_threshold", "0", str(path)]
    subprocess.run(args, check=True)
    return path


def pixel(path, x: int, y: int, at: float = 0.0) -> tuple[int, int, int]:
    """取 at 秒那一格的某個像素（RGB）。"""
    info = ffmpeg.probe(path)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    off = (y * info["width"] + x) * 3
    return raw[off], raw[off + 1], raw[off + 2]


def dominant(rgb: tuple[int, int, int]) -> str:
    """像素偏哪個顏色：r／g／b，不夠鮮明回 '?'。"""
    r, g, b = rgb
    top = max(rgb)
    if top < 120 or sorted(rgb)[-2] > top * 0.5:
        return "?"
    return "rgb"[rgb.index(top)]


# ------------------------------------------------------------------ 看成片裡有什麼

def count_frames(path) -> int:
    """影像軌實際解出幾幀（不信 metadata）。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout
    return int(out.strip())


def peak(path, start: float, length: float, *, band: float | None = None) -> int:
    """音訊在 [start, start+length] 這段的最大振幅（16-bit；沒有音訊軌就是 0）。

    `band` 給一個頻率（Hz）就只看那個頻率附近：測試用不同頻率的純音當「旁白」與「音樂」，
    這樣能在混好的聲音裡分開量。
    """
    af = ["-af", f"bandpass=f={band}:width_type=q:w=20"] if band else []
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "8000", *af, "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    return max((abs(v) for v in array.array("h", raw)), default=0)


def tones(path, parts: Sequence[tuple[float, float]], *, amp: float = 0.25, rate: int = 48000,
          channels: int = 2, delay: float = 0.0, total: float | None = None):
    """依序播純音：parts 是 [(頻率 Hz, 秒數), …]；`delay` 前面補靜音、`total` 後面補到這麼長。

    副檔名決定格式（.wav／.m4a）。用來做測試的旁白與背景音樂。
    """
    args = ["ffmpeg", "-v", "error", "-y"]
    for freq, seconds in parts:
        args += ["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:r={rate}"]
    chain = [f"concat=n={len(parts)}:v=0:a=1", f"volume={amp * 8}"]  # lavfi sine 預設振幅 1/8
    if delay:
        chain.append(f"adelay={int(delay * 1000)}:all=1")
    if total:
        chain.append(f"apad=whole_dur={total}")
    pads = "".join(f"[{n}:a]" for n in range(len(parts)))
    args += ["-filter_complex", f"{pads}{','.join(chain)}[a]", "-map", "[a]", "-ac", str(channels)]
    if str(path).endswith(".m4a"):
        args += ["-c:a", "aac"]
    subprocess.run([*args, str(path)], check=True)
    return path


def has_cjk_font() -> bool:
    if not shutil.which("fc-list"):
        return False
    out = subprocess.run(["fc-list", ":lang=zh"], capture_output=True, text=True).stdout
    return bool(out.strip())


def font_name() -> str:
    out = subprocess.run(["fc-list", ":lang=zh", "family"], capture_output=True, text=True).stdout
    return out.splitlines()[0].split(",")[0].strip()


def bright_rows(video, at: float, width: int, height: int) -> list[int]:
    """這一格裡有「亮色（字）」的橫列，用來看字幕出現在畫面的哪個高度。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return [y for y in range(height) if any(v > 200 for v in raw[y * width:(y + 1) * width])]
