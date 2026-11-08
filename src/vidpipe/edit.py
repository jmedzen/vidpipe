"""裁切、補長、串接。輸入是 `normalize` 過的片段（統一規格），輸出也維持同一規格。

- `trim`：精準裁切。一律重編碼，不用 `-c copy`——copy 只能切在關鍵幀上，會差到好幾秒。
- `fill`：湊成指定長度。素材夠長就裁；不夠就循環（或 `loop=False` 直接報錯讓呼叫端換素材）。
- `concat`：串接。先驗證每一支規格一致（不一致就告訴你哪一支差在哪），預設用 concat
  濾鏡重編碼，接縫處音畫都準；`copy=True` 走 demuxer 不重編碼，快而且零畫質損失，
  但 AAC 在接縫處可能有幾十毫秒的誤差，只適合沒有音訊或不在意的場合。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import ffmpeg
from .errors import BadInput
from .video import OutputSpec, encode_args

# 比素材長度多一點點的要求當作剛好（ffprobe 的時長本來就有幾十毫秒的誤差）
TOLERANCE = 0.05


class ClipTooShort(BadInput):
    code = "clip_too_short"


def _spec_like(info: dict[str, Any]) -> OutputSpec:
    """沒指定規格時，沿用輸入的 fps（裁切不該偷偷改 fps）。"""
    return OutputSpec(width=info["width"] or 1080, height=info["height"] or 1920,
                      fps=round(info["fps"] or 30))


def _check_video(info: dict[str, Any], path: str | Path) -> None:
    if not info["has_video"]:
        raise BadInput(f"{Path(path).name} 沒有影像軌", hint="這一步只處理影片")


def _check_window(info: dict[str, Any], start: float, duration: float | None) -> float:
    """檢查 start／duration，回傳實際長度。"""
    total = info["duration"]
    if start < 0:
        raise BadInput(f"start 不能是負的（收到 {start}）")
    if start >= total - 0.01:
        raise BadInput(f"start={start:g} 已經超過素材長度 {total:g}s")
    if duration is None:
        return total - start
    if duration <= 0:
        raise BadInput(f"duration 要大於 0（收到 {duration}）")
    return duration


def _atomic(dest: str | Path):
    """回傳 (dest, 暫存檔)。成功後呼叫端 `tmp.replace(dest)`；失敗不留半成品。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest, dest.with_name(f".{dest.stem}.partial{dest.suffix}")


def trim(src: str | Path, dest: str | Path, start: float = 0.0, duration: float | None = None, *,
         spec: OutputSpec | None = None, timeout: float = 900.0) -> dict[str, Any]:
    """從 start 秒起裁 duration 秒（省略 = 到尾）。回傳輸出檔的 probe。"""
    info = ffmpeg.probe(src)
    _check_video(info, src)
    length = _check_window(info, start, duration)
    if start + length > info["duration"] + TOLERANCE:
        raise ClipTooShort(
            f"{Path(src).name} 只有 {info['duration']:g}s，裁不出 {start:g}+{length:g}s",
            hint="用 `fill` 可以循環補長；或換一支比較長的素材")
    spec = spec or _spec_like(info)
    dest, tmp = _atomic(dest)
    args = ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}",
            "-map", "0:v", "-map", "0:a?", *encode_args(spec), "-r", str(spec.fps),
            "-movflags", "+faststart", str(tmp)]
    try:
        ffmpeg.run(args, timeout=timeout)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return ffmpeg.probe(dest)


def fill(src: str | Path, dest: str | Path, duration: float, *, start: float = 0.0,
         loop: bool = True, spec: OutputSpec | None = None,
         timeout: float = 900.0) -> dict[str, Any]:
    """湊成剛好 duration 秒：夠長就從 start 裁，不夠就把 start 之後的部分循環。

    回傳輸出檔的 probe，多一個 `looped`（有沒有循環）。`loop=False` 時素材不夠長丟
    `ClipTooShort`，讓呼叫端換下一支素材。
    """
    info = ffmpeg.probe(src)
    _check_video(info, src)
    _check_window(info, start, duration)
    available = info["duration"] - start
    if available + TOLERANCE >= duration:
        out = trim(src, dest, start, duration, spec=spec, timeout=timeout)
        return {**out, "looped": False}
    if not loop:
        raise ClipTooShort(
            f"{Path(src).name} 從 {start:g}s 起只剩 {available:g}s，不夠 {duration:g}s",
            hint="換一支比較長的素材，或允許循環（loop=True）")

    spec = spec or _spec_like(info)
    dest, tmp = _atomic(dest)
    segment = None
    source = Path(src)
    try:
        if start > 0:
            # 只循環 start 之後的部分：先精準裁出來，再對這一小段 stream_loop
            segment = dest.with_name(f".{dest.stem}.segment.mp4")
            trim(src, segment, start, None, spec=spec, timeout=timeout)
            source = segment
        args = ["-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}",
                "-map", "0:v", "-map", "0:a?", *encode_args(spec), "-r", str(spec.fps),
                "-movflags", "+faststart", str(tmp)]
        ffmpeg.run(args, timeout=timeout)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
        if segment is not None:
            segment.unlink(missing_ok=True)
    return {**ffmpeg.probe(dest), "looped": True}


def _describe(info: dict[str, Any]) -> str:
    return (f"{info['width']}x{info['height']}@{info['fps']:g}fps "
            f"{info['vcodec']}/{info['pix_fmt']} {info['acodec']}/{info['sample_rate']}Hz")


def check_uniform(infos: Sequence[dict[str, Any]], *, copy: bool = False) -> None:
    """每一支都要有影音，且尺寸、fps、取樣率一致；copy 模式連編碼都要一樣。"""
    base = infos[0]
    for info in infos:
        name = Path(info["path"]).name
        if not info["has_video"]:
            raise BadInput(f"{name} 沒有影像軌")
        if not info["has_audio"]:
            raise BadInput(f"{name} 沒有音軌", hint="先用 `vidpipe normalize` 轉過（會補靜音軌）")
    for info in infos[1:]:
        same = (info["width"], info["height"], info["sample_rate"], info["channels"]) == \
               (base["width"], base["height"], base["sample_rate"], base["channels"]) \
               and abs((info["fps"] or 0) - (base["fps"] or 0)) < 0.01
        if copy:
            same = same and (info["vcodec"], info["pix_fmt"], info["acodec"]) == \
                            (base["vcodec"], base["pix_fmt"], base["acodec"])
        if not same:
            raise BadInput(
                f"{Path(info['path']).name}（{_describe(info)}）跟第一支"
                f"{Path(base['path']).name}（{_describe(base)}）規格不同",
                hint="每一支先用 `vidpipe normalize` 轉成同一規格再串接")


def _list_line(path: str) -> str:
    return "file '" + str(Path(path).resolve()).replace("'", "'\\''") + "'\n"


def concat(inputs: Sequence[str | Path], dest: str | Path, *, copy: bool = False,
           timeout: float = 1800.0) -> dict[str, Any]:
    """依序串接。回傳輸出檔的 probe。規格取第一支的。"""
    if not inputs:
        raise BadInput("沒有要串接的檔案")
    infos = [ffmpeg.probe(p) for p in inputs]
    check_uniform(infos, copy=copy)
    base = infos[0]
    spec = OutputSpec(width=base["width"], height=base["height"], fps=round(base["fps"]),
                      audio_rate=base["sample_rate"])
    dest, tmp = _atomic(dest)
    listing = dest.with_name(f".{dest.stem}.list.txt")
    try:
        if copy:
            listing.write_text("".join(_list_line(i["path"]) for i in infos), encoding="utf-8")
            args = ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                    "-movflags", "+faststart", str(tmp)]
        else:
            args = []
            for info in infos:
                args += ["-i", info["path"]]
            pads = "".join(f"[{n}:v][{n}:a]" for n in range(len(infos)))
            args += ["-filter_complex", f"{pads}concat=n={len(infos)}:v=1:a=1[v][a]",
                     "-map", "[v]", "-map", "[a]", *encode_args(spec), "-r", str(spec.fps),
                     "-movflags", "+faststart", str(tmp)]
        ffmpeg.run(args, timeout=timeout)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)
    return ffmpeg.probe(dest)
