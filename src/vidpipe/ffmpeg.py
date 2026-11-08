"""ffmpeg／ffprobe 的薄包裝：執行、錯誤轉換、探測。

渲染的每一步都是一次 ffmpeg 呼叫。錯誤一律轉成結構化錯誤，訊息只留 ffmpeg 最後幾行
（完整指令太長、對 agent 沒用）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .errors import DependencyMissing, NotFound, Timeout, VidpipeError


class RenderError(VidpipeError):
    """ffmpeg 失敗。多半是輸入檔壞掉或參數不合；重試同樣的輸入不會好。"""

    code = "render_failed"
    retryable = False


def require() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise DependencyMissing(f"找不到 {tool}",
                                    hint="安裝 ffmpeg（macOS：brew install ffmpeg）")


def run(args: list[str], *, timeout: float = 600.0) -> None:
    """跑一次 ffmpeg（`args` 不含 ffmpeg 本身）。失敗丟 RenderError。"""
    require()
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Timeout(f"ffmpeg 逾時（>{int(timeout)}s）") from exc
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()][-4:]
        raise RenderError("ffmpeg 失敗：" + (" | ".join(tail) or f"exit {proc.returncode}"),
                          hint="確認輸入檔能播放；用 `vidpipe probe` 看它的串流資訊")


def _fps(text: str | None) -> float | None:
    try:
        value = float(Fraction(text or "0"))
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value > 0 else None


def probe(path: str | Path) -> dict[str, Any]:
    """影片／音訊檔的基本資訊。檔案不存在丟 NotFound，不是媒體檔丟 RenderError。"""
    require()
    path = Path(path)
    if not path.is_file():
        raise NotFound(f"找不到檔案：{path}")
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
             str(path)],
            capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Timeout("ffprobe 逾時") from exc
    if proc.returncode != 0:
        raise RenderError(f"不是可讀的媒體檔：{path.name}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration")
                     or (video or {}).get("duration") or (audio or {}).get("duration") or 0)
    return {
        "path": str(path),
        "duration": round(duration, 3),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "width": int(video["width"]) if video and video.get("width") else None,
        "height": int(video["height"]) if video and video.get("height") else None,
        "fps": _fps((video or {}).get("avg_frame_rate")),
        "vcodec": (video or {}).get("codec_name"),
        "pix_fmt": (video or {}).get("pix_fmt"),
        "acodec": (audio or {}).get("codec_name"),
        "sample_rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": int(audio["channels"]) if audio and audio.get("channels") else None,
        "size_bytes": path.stat().st_size,
    }
