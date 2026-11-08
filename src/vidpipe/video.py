"""統一編碼與重構圖：任何來源的影片 → 同一種規格，之後才能直接串接。

統一規格（`OutputSpec`）：H.264（yuv420p、固定 fps、方形像素）＋ AAC（48kHz 立體聲）。
**每個輸出一定有音軌**——來源沒有音軌、或要求靜音時補一條靜音軌，這樣之後 concat 時
所有片段的串流組成一致，不會因為其中一支沒有音軌而斷掉。

重構圖（`fit`）：
- `crop`：放大到蓋滿再裁；`focus=(x, y)`（0–1）決定裁哪一塊，預設置中
- `blur_pad`：背景是同一支影片放大＋模糊，原片完整放在中間（不裁掉任何內容）
- `letterbox`：原片完整放在中間，上下或左右補黑邊
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ffmpeg
from .errors import BadInput

FITS = ("crop", "blur_pad", "letterbox")
AUDIO_MODES = ("keep", "silent")

# 長寬比 → (寬, 高)。Shorts 用 9:16。
ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


@dataclass(frozen=True)
class OutputSpec:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 20
    preset: str = "veryfast"
    audio_rate: int = 48000
    audio_bitrate: str = "192k"

    def __post_init__(self) -> None:
        if self.width < 16 or self.height < 16 or self.width % 2 or self.height % 2:
            raise BadInput(f"輸出尺寸要是 16 以上的偶數（收到 {self.width}x{self.height}）",
                           hint="yuv420p 需要偶數寬高")
        if not 1 <= self.fps <= 120:
            raise BadInput(f"fps 要在 1–120（收到 {self.fps}）")

    @classmethod
    def for_aspect(cls, aspect: str, **kw: Any) -> OutputSpec:
        if aspect not in ASPECTS:
            raise BadInput(f"不認得的長寬比：{aspect}", hint=f"可用：{', '.join(ASPECTS)}")
        w, h = ASPECTS[aspect]
        return cls(width=w, height=h, **kw)


def parse_focus(value: str | tuple[float, float] | None) -> tuple[float, float]:
    """`0.3,0.5` 或 (0.3, 0.5) → 兩個 0–1 的數；None → 置中。"""
    if value is None or value == "":
        return 0.5, 0.5
    try:
        if isinstance(value, str):
            x, y = (float(p) for p in value.split(","))
        else:
            x, y = float(value[0]), float(value[1])
    except (ValueError, TypeError, IndexError):
        raise BadInput(f"focus 要寫成 x,y（0–1），收到 {value!r}", hint="例如 0.5,0.5 或 0.3,0.4") \
            from None
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise BadInput(f"focus 要在 0–1 之間（收到 {x},{y}）")
    return x, y


def video_filter(src_w: int, src_h: int, spec: OutputSpec, fit: str,
                 focus: tuple[float, float] = (0.5, 0.5)) -> str:
    """產生影像的 filtergraph（輸入標籤 [0:v]，輸出標籤 [v]）。純字串，方便測試。"""
    if fit not in FITS:
        raise BadInput(f"不認得的 fit：{fit}", hint=f"可用：{', '.join(FITS)}")
    w, h = spec.width, spec.height
    tail = f"setsar=1,fps={spec.fps},format=yuv420p"
    # 長寬比已經對上（±1%）：不管哪種 fit 都只是縮放，不必裁也不必補
    if abs(src_w / src_h - w / h) / (w / h) <= 0.01:
        return f"[0:v]scale={w}:{h}:flags=lanczos,{tail}[v]"
    fx, fy = focus
    if fit == "crop":
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h}:(in_w-{w})*{fx:g}:(in_h-{h})*{fy:g},{tail}[v]")
    if fit == "letterbox":
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,{tail}[v]")
    # blur_pad：背景先縮小再放大，模糊才快；前景完整置中
    return (f"[0:v]split=2[bg0][fg0];"
            f"[bg0]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"gblur=sigma=30:steps=2[bg];"
            f"[fg0]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}[v]")


def audio_filter(spec: OutputSpec, duration: float, keep: bool) -> tuple[list[str], str]:
    """回傳 (額外的輸入參數, 音訊 filtergraph)。靜音軌用 anullsrc 產生，長度跟影片一樣。"""
    rate = spec.audio_rate
    if keep:
        return [], f"[0:a]aresample={rate},aformat=sample_fmts=fltp:channel_layouts=stereo[a]"
    src = f"anullsrc=r={rate}:cl=stereo"
    return ["-f", "lavfi", "-t", f"{max(duration, 0.1):.3f}", "-i", src], \
        "[1:a]anull[a]"


def normalize(src: str | Path, dest: str | Path, spec: OutputSpec | None = None, *,
              fit: str = "blur_pad", focus: tuple[float, float] | str | None = None,
              audio: str = "keep", timeout: float = 900.0) -> dict[str, Any]:
    """把 src 轉成統一規格存到 dest，回傳輸出檔的 probe。"""
    spec = spec or OutputSpec()
    if audio not in AUDIO_MODES:
        raise BadInput(f"不認得的 audio 模式：{audio}", hint=f"可用：{', '.join(AUDIO_MODES)}")
    info = ffmpeg.probe(src)
    if not info["has_video"] or not info["width"] or not info["height"]:
        raise BadInput(f"{Path(src).name} 沒有影像軌", hint="這一步只處理影片；音訊請用 mix")
    fx = parse_focus(focus)
    keep_audio = audio == "keep" and info["has_audio"]
    vf = video_filter(info["width"], info["height"], spec, fit, fx)
    extra_in, af = audio_filter(spec, info["duration"], keep_audio)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
    args = ["-i", str(src), *extra_in,
            "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf),
            "-c:a", "aac", "-b:a", spec.audio_bitrate, "-ar", str(spec.audio_rate), "-ac", "2",
            "-video_track_timescale", str(spec.fps * 512),
            "-t", f"{info['duration']:.3f}",
            "-movflags", "+faststart", str(tmp)]
    try:
        ffmpeg.run(args, timeout=timeout)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return ffmpeg.probe(dest)
