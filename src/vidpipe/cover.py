"""封面圖：從成片的畫面挑一格，疊上標題，存成 JPEG。

- 畫面取自**還沒燒字幕**的場景片段（`render/segments.txt`），所以標題不會跟字幕疊在一起。
- `at`（秒）預設 1.0（短片取中間）；標題預設用 manifest 的 `title`，`text: ""` 就不放字。
- 標題用跟字幕同一套字體與折行（`subtitle_font`）：上緣 18%、最多三行、白字黑邊。
- 尺寸跟成片一致（9:16 就是 1080×1920）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ffmpeg, subtitles
from .config import Config
from .errors import BadInput
from .ffmpeg import filter_value
from .manifest import Manifest

TITLE_SCALE = 0.09      # 字級 = 短邊 × 這個數（比字幕大）
MAX_TITLE_LINES = 3
TOP_MARGIN = 0.18       # 標題上緣離畫面頂端的比例
DEFAULT_AT = 1.0


def title_ass(text: str, spec: Any, font: str) -> str:
    """一個整段時間都顯示的標題字幕（ASS），疊在選出來的那一格上。"""
    size = round(min(spec.width, spec.height) * TITLE_SCALE)
    side = round(spec.width * 0.07)
    lines = subtitles.wrap(text, (spec.width - 2 * side) / size)
    if len(lines) > MAX_TITLE_LINES:
        lines = lines[:MAX_TITLE_LINES]
        lines[-1] = lines[-1].rstrip("，、；：。") + "…"
    body = "\\N".join(subtitles._ass_text(line) for line in lines)
    outline = max(3, round(size * 0.10))
    return "\n".join([
        "[Script Info]", "; 由 vidpipe 產生", "ScriptType: v4.00+",
        f"PlayResX: {spec.width}", f"PlayResY: {spec.height}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,"
        f"100,100,0,0,1,{outline},2,8,{side},{side},{round(spec.height * TOP_MARGIN)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,0:00:00.00,0:10:00.00,Title,,0,0,0,,{body}", ""])


def make(manifest: Manifest, cfg: Config, listing: Path, total: float, dest: Path, *,
         at: float | None = None, text: str | None = None,
         timeout: float = 120.0) -> dict[str, Any]:
    """`listing`（場景片段的 concat 清單）裡 `at` 秒那一格 → `dest`（JPEG）。"""
    cover = manifest.cover or {}
    moment = at if at is not None else cover.get("at", min(DEFAULT_AT, total / 2))
    if moment > total:
        raise BadInput(f"封面的 at={moment:g} 超過整支片的長度 {total:g}s",
                       hint="at 要在影片長度之內（秒）")
    moment = min(moment, max(0.0, total - 0.1))   # 最後一格常常是空的
    label = text if text is not None else cover.get("text", manifest.title)
    flt = ["format=yuvj420p"]
    work = dest.parent
    ass = work / f".{dest.stem}.title.ass"
    try:
        if label.strip():
            ass.write_text(title_ass(label, manifest.output, cfg.subtitle_font),
                           encoding="utf-8")
            flt.insert(0, f"ass={filter_value(str(ass))}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
        try:
            # -ss 放在 -i 後面：concat 不支援輸入端的快轉（會默默給第一格）
            ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-ss", f"{moment:.3f}",
                        "-frames:v", "1", "-vf", ",".join(flt), "-q:v", "2", "-update", "1",
                        str(tmp)], timeout=timeout)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    finally:
        ass.unlink(missing_ok=True)
    return {"path": str(dest), "at": round(moment, 3), "text": label.strip() or None,
            "width": manifest.output.width, "height": manifest.output.height}
