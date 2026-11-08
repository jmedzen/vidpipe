"""渲染：manifest ＋ `narrate` 算好的時間軸 → 成片 mp4。

    manifest ──┬─ 每個場景：取素材（assets）→ 裁切、補長、重構圖 → 無聲的場景片段（快取）
               └─ timeline.json（每個場景幾幀）、narration.wav、subs.ass
    場景片段 ── 串接（不重編碼）──┬─ 燒字幕（重編碼）或直接複製影像
    narration.wav ＋ 背景音樂 ── 混音、響度標準化（mix.py）─┴─ 編成 AAC ──→ out/<id>.mp4

- 聲音在 `mix.py`：旁白、背景音樂（可壓低在旁白底下）、整支片的響度標準化。
- **每個場景一個片段**，檔名帶內容雜湊（素材檔、起點、幀數、規格、fit、focus）。重跑時沒動的場景
  直接用快取，只重做改過的；舊版本的片段在渲染結束時清掉。
- 場景長度是 `timeline.json` 裡的整數幀，所以片段是精確的幀數，串接是 `-c copy`，
  不會有音畫位移、也不會多一輪畫質損失。
- 素材不夠長就循環補長（只循環 `start` 之後的部分），並在 `warnings` 提醒。
  沒有 `clip` 的場景用黑畫面。
- `render` 會檢查 timeline.json 是不是從**現在這份** manifest（加上音色、字幕設定）算出來的；
  改過就要求先重跑 `narrate`，不會用過期的旁白與字幕默默渲染出錯的片。
- 中間檔留在 `work_dir/shorts/<id>/render/`，方便除錯。`sources.json` 記下每個場景用了哪個素材、
  出處與授權，之後 credits 用它。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import assets, edit, ffmpeg, jobs, mix, timeline
from .assets import Resolved
from .config import Config
from .errors import BadInput
from .manifest import Manifest, Scene
from .video import OutputSpec, audio_encode_args, video_encode_args, video_filter

SEGMENT_VERSION = "seg-v1"
SAFE_LICENSES = ("free", "free-attribution")   # vidgrab 的授權分類；其餘（check／unknown）要提醒


def filter_value(path: str) -> str:
    """路徑 → ffmpeg 濾鏡參數裡能安全使用的寫法（空白、冒號、單引號、逗號、括號都可以）。"""
    level1 = path.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    return "'" + level1.replace("'", "'\\''") + "'"


@dataclass(frozen=True)
class Segment:
    path: Path
    frames: int
    looped: bool
    cached: bool
    available: float | None = None   # 素材從起點起剩幾秒（只有循環時有意義）


def _key(parts: dict[str, Any]) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _encode_video(args: list[str], spec: OutputSpec, dest: Path, *, frames: int,
                  timeout: float) -> None:
    tmp = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
    try:
        ffmpeg.run([*args, "-frames:v", str(frames), *video_encode_args(spec),
                    "-r", str(spec.fps), "-an", "-movflags", "+faststart", str(tmp)],
                   timeout=timeout, duration=frames / spec.fps)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def build_segment(scene: Scene, index: int, frames: int, resolved: Resolved | None,
                  spec: OutputSpec, seg_dir: Path, *, timeout: float = 900.0) -> Segment:
    """一個場景 → 無聲、剛好 `frames` 幀的片段。同樣的輸入已經做過就直接用。"""
    seg_dir.mkdir(parents=True, exist_ok=True)
    parts: dict[str, Any] = {"v": SEGMENT_VERSION, "frames": frames, "w": spec.width,
                             "h": spec.height, "fps": spec.fps, "crf": spec.crf,
                             "preset": spec.preset}
    if resolved is None:
        parts["src"] = "black"
    else:
        st = Path(resolved.path).stat()
        parts.update(src=resolved.path, size=st.st_size, mtime=st.st_mtime_ns,
                     offset=round(resolved.offset, 3), fit=scene.fit, focus=list(scene.focus))
    dest = seg_dir / f"{index:02d}-{scene.id}-{_key(parts)}.mp4"

    if resolved is None:
        existed = dest.is_file()
        if not existed:
            _encode_video(["-f", "lavfi", "-i",
                           f"color=c=black:s={spec.width}x{spec.height}:r={spec.fps}",
                           "-vf", "setsar=1"], spec, dest, frames=frames, timeout=timeout)
        return Segment(dest, frames, False, existed)

    info = ffmpeg.probe(resolved.path)
    where = f"scenes[{index}].clip"
    if resolved.offset >= info["duration"] - 0.05:
        raise BadInput(f"{where}：start={resolved.offset:g} 已經超過素材長度 {info['duration']:g}s",
                       hint="縮小 start，或換一支比較長的素材")
    available = info["duration"] - resolved.offset
    wanted = frames / spec.fps
    looped = available + edit.TOLERANCE < wanted
    if dest.is_file():
        return Segment(dest, frames, looped, True, available if looped else None)

    vf = video_filter(info["width"], info["height"], spec, scene.fit, scene.focus)
    seek = ["-ss", f"{resolved.offset:.3f}", "-i", resolved.path, "-filter_complex", vf,
            "-map", "[v]"]
    if not looped:
        _encode_video(seek, spec, dest, frames=frames, timeout=timeout)
        return Segment(dest, frames, False, False)
    # 素材不夠長：先把「起點之後的部分」做成規格一致的片段，再對它循環
    part = dest.with_name(f".{dest.stem}.part{dest.suffix}")
    try:
        _encode_video(seek, spec, part, frames=max(1, int(available * spec.fps)),
                      timeout=timeout)
        _encode_video(["-stream_loop", "-1", "-i", str(part)], spec, dest, frames=frames,
                      timeout=timeout)
    finally:
        part.unlink(missing_ok=True)
    return Segment(dest, frames, True, False, available)


def _mux(listing: Path, audio: mix.AudioPlan, subs: Path | None, spec: OutputSpec,
         total: float, dest: Path, *, timeout: float) -> None:
    """串接片段（不重編碼）＋聲音 → AAC；要燒字幕就同時重編碼影像。"""
    args = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    if audio.path is not None:
        args += ["-i", str(audio.path)]
        lead = audio.filter + "," if audio.filter else ""
        chain = (f"[1:a]{lead}aresample={spec.audio_rate},"
                 f"aformat=sample_fmts=fltp:channel_layouts=stereo[a]")
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i",
                 f"anullsrc=r={spec.audio_rate}:cl=stereo"]
        chain = "[1:a]anull[a]"
    if subs is not None:
        graph = f"[0:v]ass={filter_value(str(subs))}[v];{chain}"
        video_map, video_enc = "[v]", video_encode_args(spec)
    else:
        graph, video_map, video_enc = chain, "0:v", ["-c:v", "copy"]
    tmp = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
    try:
        ffmpeg.run([*args, "-filter_complex", graph, "-map", video_map, "-map", "[a]",
                    *video_enc, *audio_encode_args(spec), "-t", f"{total:.3f}",
                    "-movflags", "+faststart", str(tmp)], timeout=timeout, duration=total)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _license_warning(label: str, credit: dict[str, Any]) -> str | None:
    if credit.get("kind") != "asset" or \
            (credit.get("license") or {}).get("kind") in SAFE_LICENSES:
        return None
    return (f"{label}的素材授權未明確（{credit.get('asset_id')}）：公開前請先確認；"
            f"{credit.get('credit_hint', '')}").rstrip("：；")


def run(manifest: Manifest, raw: Any, cfg: Config, out: str | Path | None = None, *,
        make_client=assets.make_client, timeout: float = 900.0) -> dict[str, Any]:
    """照 manifest 渲染成片，回結果（輸出檔、每個場景用了什麼、warnings）。"""
    tl = timeline.load(cfg, manifest.id)
    if tl.get("fingerprint") != timeline.fingerprint(manifest, raw, cfg):
        raise BadInput(
            "timeline.json 不是從現在這份 manifest 算出來的（manifest、音色或字幕設定改過）",
            hint="重跑 `vidpipe narrate <manifest>`；沒改的句子走快取，很快")
    spec = manifest.output
    if tl["fps"] != spec.fps or len(tl["scenes"]) != len(manifest.scenes):
        raise BadInput("timeline.json 跟 manifest 的場景對不上", hint="重跑 `vidpipe narrate`")
    root = timeline.short_dir(cfg, manifest.id)
    render_dir = root / "render"
    seg_dir = render_dir / "segments"
    warnings: list[str] = []

    # 先把每個場景的素材都備齊：網路的問題在花時間編碼之前就浮現
    music_cfg = mix.music_settings(manifest.music)
    jobs.stage("assets", f"{sum(1 for s in manifest.scenes if s.clip) + bool(music_cfg)} 個素材")
    resolved: list[Resolved | None] = []
    for i, (scene, span) in enumerate(zip(manifest.scenes, tl["scenes"], strict=True)):
        resolved.append(assets.resolve(scene.clip, span["frames"] / spec.fps,
                                       cfg, where=f"scenes[{i}].clip", make=make_client))
        jobs.progress((i + 1) / len(manifest.scenes))
    music = None
    if music_cfg is not None:
        music = assets.resolve_audio(manifest.music or {}, tl["total"], cfg, where="music",
                                     make=make_client)

    jobs.stage("segments", f"{len(manifest.scenes)} 個場景")
    segments: list[Segment] = []
    for i, (scene, span, res) in enumerate(zip(manifest.scenes, tl["scenes"], resolved,
                                               strict=True)):
        seg = build_segment(scene, i, span["frames"], res, spec, seg_dir, timeout=timeout)
        segments.append(seg)
        jobs.log(f"場景 {scene.id}：{span['frames']} 幀" + ("（用快取）" if seg.cached else ""))
        jobs.progress((i + 1) / len(manifest.scenes))
        if res is None:
            warnings.append(f"場景 {scene.id} 沒有 clip，用黑畫面")
        elif seg.looped:
            wanted = span["frames"] / spec.fps
            warnings.append(f"場景 {scene.id} 的素材從 {res.offset:g} 秒起只剩 "
                            f"{seg.available:.1f} 秒，不夠 {wanted:.1f} 秒，已循環補長")
        if not scene.mute_source:
            warnings.append(f"場景 {scene.id} 的 mute_source=false 還沒支援，先靜音")
        warning = _license_warning(f"場景 {scene.id} ", res.credit if res else {})
        if warning:
            warnings.append(warning)

    # 清掉這支片舊版本的片段（改過的場景會留下舊雜湊的檔案）
    keep = {s.path.name for s in segments}
    for old in seg_dir.glob("*.mp4"):
        if old.name not in keep:
            old.unlink(missing_ok=True)

    listing = render_dir / "segments.txt"
    listing.write_text("".join(edit.list_line(str(s.path)) for s in segments), encoding="utf-8")
    narration = root / "narration.wav" if (tl.get("narration") or {}).get("file") else None
    if manifest.music and music_cfg is None:
        warnings.append("music.volume 是 0，沒有混入音樂")
    if music is not None:
        warning = _license_warning("背景音樂", music.credit)
        if warning:
            warnings.append(warning)
    jobs.stage("audio", "混音、量響度" if music else "量響度")
    audio = mix.plan(narration, music, music_cfg, tl["total"], spec, cfg, render_dir,
                     timeout=timeout)
    warnings.extend(audio.warnings)
    sub_info = tl.get("subtitles")
    burn = bool(sub_info) and manifest.subtitles.burn_in
    dest = Path(out) if out else Path(cfg.out_dir) / f"{manifest.id}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    jobs.stage("mux", "燒字幕、混入旁白" if burn else "混入旁白")
    _mux(listing, audio, root / sub_info["ass"] if burn else None, spec, tl["total"], dest,
         timeout=timeout)

    srt_path = None
    if sub_info and not burn:  # 不燒字幕：另外給字幕檔
        srt_path = dest.with_suffix(".srt")
        shutil.copyfile(root / sub_info["srt"], srt_path)

    sources = [{"scene": s.id, "offset": r.offset if r else None, "looped": seg.looped,
                "source": r.credit if r else None}
               for s, r, seg in zip(manifest.scenes, resolved, segments, strict=True)]
    music_source = None if music is None else {
        "offset": music.offset, "looped": bool((audio.info.get("music") or {}).get("looped")),
        "source": music.credit}
    (root / "sources.json").write_text(json.dumps(
        {"schema": "vidpipe.sources/1", "short_id": manifest.id, "scenes": sources,
         "music": music_source}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    info = ffmpeg.probe(dest)
    audio_info = dict(audio.info)
    if audio_info.get("loudness") and info["has_audio"]:
        final = mix.measure(dest, timeout=timeout)
        audio_info["loudness"] = {**audio_info["loudness"],
                                  "output": round(final["i"], 1) if final else None}
    return {"short_id": manifest.id, "path": str(dest), "duration": info["duration"],
            "width": info["width"], "height": info["height"], "fps": info["fps"],
            "size_bytes": dest.stat().st_size, "burned_subtitles": burn, "audio": audio_info,
            "srt": str(srt_path) if srt_path else None,
            "scenes": [{"id": s.id, "frames": seg.frames, "looped": seg.looped,
                        "cached": seg.cached,
                        "source": (r.credit.get("asset_id") or r.credit.get("path")) if r else None}
                       for s, r, seg in zip(manifest.scenes, resolved, segments, strict=True)],
            "warnings": warnings}
