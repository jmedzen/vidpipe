"""時間軸：每一句旁白的實際秒數 → 場景長度、字幕時間、整段旁白音軌、字幕檔。

規則（秒，`manifest.timing` 可調）：

    場景開頭 ─ lead ─ 第一句 ─ gap ─ 第二句 ─ … ─ 最後一句 ─ tail ─ 場景結束

- 場景長度 = max(`min_duration`, 旁白結束 + tail)，**向上取整到整數幀**。所以每個場景的
  起點都落在幀邊界上，之後逐場景渲染再串接不會累積誤差。沒有旁白的場景就是 `min_duration`。
- 句子的起訖是絕對時間（從整支片開頭算），字幕直接用。
- `narration.wav` 把每句旁白擺在它的起點、其餘補靜音，長度等於整支片。之後渲染只要把它當
  一條音軌混進去，旁白跟字幕永遠對得上。

產物放在 `work_dir/shorts/<id>/`：`manifest.json`（驗證過的原稿）、`timeline.json`、
`narration.wav`、`subs.srt`、`subs.ass`（字幕，見 `subtitles`）。重跑同一份 manifest 會得到
同樣的內容（旁白走快取）。
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ffmpeg, jobs, narration, subtitles
from .config import Config
from .errors import BadInput, NotFound, UpstreamError
from .manifest import Manifest
from .narration import Clip

SCHEMA = "vidpipe.timeline/1"
SOFT_LIMIT = 60.0    # 秒。Shorts 傳統的長度；超過只提醒
HARD_LIMIT = 180.0   # 秒。超過 3 分鐘就不會被當成 Shorts
SRT_FILE = "subs.srt"
ASS_FILE = "subs.ass"


@dataclass(frozen=True)
class Cue:
    """一句旁白（也是一行字幕）。"""

    index: int           # 整支片的第幾句（從 0）
    scene_id: str
    text: str
    en: str
    start: float         # 絕對時間（秒）
    end: float
    clip: str            # 這一句的 WAV 路徑

    def to_dict(self) -> dict[str, Any]:
        # `timeline.json` 只放檔名（在 work_dir/cache/tts/ 底下）：換一台機器或掛載點也還能用，
        # 而且同一份 manifest 重跑得到逐位元組相同的檔案（快取命中與否不寫進去）
        return {"index": self.index, "scene": self.scene_id, "text": self.text, "en": self.en,
                "start": self.start, "end": self.end, "clip": Path(self.clip).name}


@dataclass(frozen=True)
class Span:
    """一個場景佔的時間。"""

    id: str
    index: int
    start_frame: int
    frames: int
    start: float
    duration: float
    speech_end: float | None   # 旁白結束（絕對時間）；沒有旁白是 None
    cues: tuple[Cue, ...]

    @property
    def end(self) -> float:
        return round(self.start + self.duration, 3)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "index": self.index, "start_frame": self.start_frame,
                "frames": self.frames, "start": self.start, "end": self.end,
                "duration": self.duration, "speech_end": self.speech_end,
                "sentences": [c.to_dict() for c in self.cues]}


@dataclass(frozen=True)
class Timeline:
    short_id: str
    fps: int
    scenes: tuple[Span, ...]
    warnings: tuple[str, ...] = ()

    @property
    def total_frames(self) -> int:
        return sum(s.frames for s in self.scenes)

    @property
    def total(self) -> float:
        return round(self.total_frames / self.fps, 3)

    @property
    def cues(self) -> list[Cue]:
        return [c for s in self.scenes for c in s.cues]

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "short_id": self.short_id, "fps": self.fps,
                "total_frames": self.total_frames, "total": self.total,
                "warnings": list(self.warnings),
                "scenes": [s.to_dict() for s in self.scenes]}


def sentence_texts(manifest: Manifest) -> list[str]:
    """整支片依序的旁白句（`narration.synthesize` 的輸入）。"""
    return [s.text for scene in manifest.scenes for s in scene.sentences]


def _frames_for(seconds: float, fps: int) -> int:
    # 1e-6 吸收浮點誤差：剛好 2.0 秒不該因為 60.0000000001 幀而多出一幀
    return max(1, math.ceil(seconds * fps - 1e-6))


def build(manifest: Manifest, clips: Sequence[Clip]) -> Timeline:
    """manifest + 每句旁白的實際長度 → 時間軸。`clips` 要跟 `sentence_texts` 同順序。"""
    expected = sentence_texts(manifest)
    if [c.text for c in clips] != expected:
        raise ValueError("clips 跟 manifest 的句子對不上")  # 呼叫端的 bug，不是使用者輸入
    fps, timing = manifest.output.fps, manifest.timing
    spans: list[Span] = []
    cursor_frame = 0
    n = 0
    for si, scene in enumerate(manifest.scenes):
        start = cursor_frame / fps
        rel = timing.lead
        cues: list[Cue] = []
        for k, sentence in enumerate(scene.sentences):
            clip = clips[n]
            if k:
                rel += timing.gap
            cues.append(Cue(n, scene.id, sentence.text, sentence.en,
                            round(start + rel, 3), round(start + rel + clip.duration, 3),
                            clip.path))
            rel += clip.duration
            n += 1
        need = scene.min_duration
        if cues:
            need = max(need, rel + timing.tail)
        frames = _frames_for(need, fps)
        spans.append(Span(scene.id, si, cursor_frame, frames, round(start, 3),
                          round(frames / fps, 3), cues[-1].end if cues else None, tuple(cues)))
        cursor_frame += frames
    timeline = Timeline(manifest.id, fps, tuple(spans))
    return Timeline(manifest.id, fps, tuple(spans), _warnings(timeline))


def _warnings(timeline: Timeline) -> tuple[str, ...]:
    out: list[str] = []
    total = timeline.total
    if total > HARD_LIMIT:
        out.append(f"總長 {total:g} 秒，超過 3 分鐘，不會被當成 Shorts")
    elif total > SOFT_LIMIT:
        out.append(f"總長 {total:g} 秒，超過 60 秒；確認這是你要的長度（Shorts 上限 3 分鐘）")
    return tuple(out)


# ------------------------------------------------------------------ 旁白音軌

def _read_pcm(path: str, rate: int, channels: int) -> bytes:
    """讀一句旁白的 16-bit PCM；格式跟目標不同就先用 ffmpeg 轉。"""
    try:
        with wave.open(path, "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (rate, channels, 2):
                return wav.readframes(wav.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise UpstreamError(f"讀不了旁白檔 {Path(path).name}：{exc}", upstream="tts",
                            retryable=False,
                            hint="刪掉 work 裡的 cache/tts 重做這一句") from exc
    with tempfile.TemporaryDirectory(prefix="vidpipe-pcm-") as tmp:
        out = Path(tmp) / "x.wav"
        ffmpeg.run(["-i", path, "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le",
                    str(out)])
        with wave.open(str(out), "rb") as wav:
            return wav.readframes(wav.getnframes())


def write_narration(timeline: Timeline, dest: str | Path) -> dict[str, Any]:
    """把每句旁白擺在它的起點，其餘補靜音，寫成一個 WAV（格式取第一句的）。"""
    cues = timeline.cues
    if not cues:
        raise BadInput("這份 manifest 沒有任何旁白", hint="至少一個場景要有 text 或 sentences")
    with wave.open(cues[0].clip, "rb") as first:
        rate, channels = first.getframerate(), first.getnchannels()
    frame_bytes = 2 * channels
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
    pcm: dict[str, bytes] = {}
    try:
        with wave.open(str(tmp), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(2)
            out.setframerate(rate)
            written = 0

            def pad_to(frame: int) -> None:
                nonlocal written
                if frame > written:
                    out.writeframes(b"\x00" * ((frame - written) * frame_bytes))
                    written = frame

            for cue in cues:
                if cue.clip not in pcm:
                    pcm[cue.clip] = _read_pcm(cue.clip, rate, channels)
                data = pcm[cue.clip]
                pad_to(round(cue.start * rate))
                out.writeframes(data)
                written += len(data) // frame_bytes
            pad_to(round(timeline.total * rate))
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"path": str(dest), "duration": round(written / rate, 3), "sample_rate": rate,
            "channels": channels}


# ------------------------------------------------------------------ 一次做完

def short_dir(cfg: Config, short_id: str) -> Path:
    # 放在 shorts/ 底下：work_dir 裡還有 cache、_jobs，作品 id 取名 cache 也不會撞
    return Path(cfg.work_dir) / "shorts" / short_id


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.stem}.partial{path.suffix}")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_json(path: Path, data: Any) -> None:
    _atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def fingerprint(manifest: Manifest, raw: Any, cfg: Config) -> str:
    """這份 timeline 是從什麼算出來的：manifest 原稿加上會影響結果的有效設定。

    `render` 用它確認 timeline.json 沒有過期（manifest 或音色、字幕設定改了就要重跑 narrate）。
    """
    effective = {"manifest": raw, "voice": {"model": cfg.tts_model, "voice_ref": cfg.voice_ref,
                                            "reference_text": cfg.reference_text},
                 "subtitles": {"langs": manifest.subtitles.langs, "style": manifest.subtitles.style,
                               "burn_in": manifest.subtitles.burn_in, "font": cfg.subtitle_font}}
    text = json.dumps(effective, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(cfg: Config, short_id: str) -> dict[str, Any]:
    """讀 `narrate` 寫的 timeline.json；沒有就請使用者先跑 narrate。"""
    path = short_dir(cfg, short_id) / "timeline.json"
    if not path.is_file():
        raise NotFound(f"還沒有 {short_id} 的時間軸", hint="先跑 `vidpipe narrate <manifest>`")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput(f"timeline.json 壞了：{exc}", hint="重跑 `vidpipe narrate`") from exc


def run(manifest: Manifest, raw: Any, cfg: Config) -> dict[str, Any]:
    """合成旁白 → 排時間軸 → 寫 `manifest.json`、`timeline.json`、`narration.wav`。

    `cfg` 要已經套用 manifest 的 voice（見 `manifest.apply_voice`）。整支片都沒有旁白
    （純音樂）也行：照 `min_duration` 排時間軸，不產生 `narration.wav`。
    """
    texts = sentence_texts(manifest)
    clips = narration.synthesize(texts, cfg) if texts else []
    jobs.stage("timeline", f"{len(manifest.scenes)} 個場景")
    timeline = build(manifest, clips)
    subs = subtitles.build(timeline, manifest.subtitles, manifest.output, cfg.subtitle_font) \
        if texts else None
    warnings = [*timeline.warnings, *(subs.warnings if subs else ())]
    for message in warnings:
        jobs.log(message, "warning")

    root = short_dir(cfg, manifest.id)
    root.mkdir(parents=True, exist_ok=True)
    _atomic_json(root / "manifest.json", raw)
    audio = write_narration(timeline, root / "narration.wav") if texts else None
    if audio is None:  # 上一版有旁白、這一版沒有：別留下過期的音軌與字幕
        for name in ("narration.wav", SRT_FILE, ASS_FILE):
            (root / name).unlink(missing_ok=True)
    if subs:
        _atomic_text(root / SRT_FILE, subs.srt)
        _atomic_text(root / ASS_FILE, subs.ass)
    data = timeline.to_dict()
    data["fingerprint"] = fingerprint(manifest, raw, cfg)
    data["warnings"] = warnings
    # 檔名而不是絕對路徑，理由同 Cue.to_dict
    data["narration"] = None if audio is None else {
        "file": "narration.wav", "duration": audio["duration"],
        "sample_rate": audio["sample_rate"], "channels": audio["channels"]}
    data["subtitles"] = None if subs is None else {
        "langs": manifest.subtitles.langs, "style": manifest.subtitles.style,
        "burn_in": manifest.subtitles.burn_in, "font": cfg.subtitle_font,
        "srt": SRT_FILE, "ass": ASS_FILE, "entries": len(subs.entries)}
    data["voice"] = {"model": cfg.tts_model, "voice_ref": cfg.voice_ref,
                     "reference_text": cfg.reference_text}
    _atomic_json(root / "timeline.json", data)
    tts = {"sentences": len(clips), "synthesized": sum(not c.cached for c in clips)}
    return {"short_id": manifest.id, "dir": str(root), "total": timeline.total,
            "scenes": [{"id": s.id, "start": s.start, "duration": s.duration,
                        "sentences": len(s.cues)} for s in timeline.scenes],
            "warnings": warnings, "narration": audio["path"] if audio else None,
            "subtitles": {"srt": str(root / SRT_FILE), "ass": str(root / ASS_FILE),
                          "entries": len(subs.entries)} if subs else None,
            "timeline": str(root / "timeline.json"), "tts": tts}
