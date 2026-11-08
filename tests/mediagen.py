"""測試用的合成影片：不靠任何外部素材，用 ffmpeg lavfi 現做。"""

from __future__ import annotations

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
