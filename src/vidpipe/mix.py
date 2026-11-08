"""混音：旁白＋背景音樂 → 響度標準化過的聲音，交給 render 編成 AAC。

    narration.wav ──┬──────────────────────────────────────────────┬─ amix ─→ mix.wav
    音樂 ── 對齊旁白的響度 × volume ── 淡入淡出 ── 旁白出聲就壓低 ──┘          （浮點，不削波）
    mix.wav（或只有旁白時的 narration.wav）── 量測 ── loudnorm（兩段式、線性）→ 目標響度

- **`volume` 是音樂相對於旁白的響度**（1 = 一樣大聲、0.3 ≈ 小 10 dB），不是原檔的振幅。
  音樂檔的母帶音量差很多（有的比 TTS 大聲 20 dB），所以先量兩邊的響度再換算增益，
  同一個 `volume` 換任何音樂都差不多。
- **ducking**（`duck_under_voice`，預設開）：旁白出聲時音樂再壓低約 10–15 dB，停頓時回升，
  用 sidechain 壓縮做，所以跟著旁白的實際音量走，不是照時間軸硬切。
- 音樂比整支片短就循環（接縫會有點突兀，所以 `warnings` 會說）；比較長就切掉，
  結尾淡出 1.5 秒（短片按比例縮短）。
- **響度標準化**：整支片量一次整合響度（LUFS），再用 loudnorm 的線性模式調到目標值
  （預設 -14，`target_lufs`）。TTS 常常偏小聲，而平台只會把過大聲的片壓小，
  不會把太小聲的片拉大。`normalize_audio=false` 可關掉。
- 混好的檔案放在 `work_dir/shorts/<id>/render/mix.wav`，方便除錯；每次渲染重做
  （純音訊，幾秒鐘）。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ffmpeg
from .assets import Resolved
from .config import Config
from .video import OutputSpec

TRUE_PEAK = -1.5            # dBTP：loudnorm 的真峰值上限
LRA = 11.0
DEFAULT_VOLUME_DUCKED = 0.3   # 有 ducking：停頓時比旁白小約 10 dB，出聲時再壓低
DEFAULT_VOLUME_FLAT = 0.1     # 沒有 ducking：全程比旁白小約 20 dB
FADE_IN = 0.5
FADE_OUT = 1.5
GAIN_LIMITS = (-60.0, 30.0)   # 音樂增益的上下限：極端安靜或極端大聲的檔案不要被放大到爆
# 旁白出聲時壓低音樂：門檻 0.02（約 -34 dBFS）、比例 10:1，一般旁白音量下約壓 10–15 dB
DUCK = "sidechaincompress=threshold=0.02:ratio=10:attack=30:release=600:makeup=1"


@dataclass(frozen=True)
class AudioPlan:
    """要編碼成成片聲音的來源與最後一道濾鏡；`path` 為 None 表示整支片沒有聲音。"""

    path: Path | None
    filter: str = ""                      # 接在讀入之後的濾鏡（loudnorm…），可以是空字串
    info: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def music_settings(music: dict[str, Any] | None) -> dict[str, Any] | None:
    """manifest 的 `music` → 補上預設值；沒有音樂或 volume 是 0（靜音）回 None。"""
    if not music:
        return None
    duck = music.get("duck_under_voice", True)
    volume = music.get("volume", DEFAULT_VOLUME_DUCKED if duck else DEFAULT_VOLUME_FLAT)
    if volume <= 0:
        return None
    return {"volume": float(volume), "duck": bool(duck), "start": float(music.get("start", 0.0))}


# ------------------------------------------------------------------ 量測

_JSON = re.compile(r"\{[^{}]*\}", re.S)


def measure(path: str | Path, *, start: float = 0.0, length: float | None = None,
            timeout: float = 600.0) -> dict[str, float] | None:
    """量一個音訊的整合響度（LUFS）。整段都是靜音（或太短量不出來）回 None。

    回 `{"i": 整合響度, "tp": 真峰值, "lra": 響度範圍, "thresh": 門檻, "offset": 偏移}`，
    就是 loudnorm 第二段需要的 measured_* 數值。
    """
    args: list[str] = []
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    if length:
        args += ["-t", f"{length:.3f}"]
    args += ["-i", str(path), "-vn", "-af",
             f"loudnorm=I=-14:TP={TRUE_PEAK:g}:LRA={LRA:g}:print_format=json", "-f", "null", "-"]
    blocks = _JSON.findall(ffmpeg.run_stderr(args, timeout=timeout))
    if not blocks:
        return None
    try:
        raw = json.loads(blocks[-1])
        got = {"i": float(raw["input_i"]), "tp": float(raw["input_tp"]),
               "lra": float(raw["input_lra"]), "thresh": float(raw["input_thresh"]),
               "offset": float(raw["target_offset"])}
    except (KeyError, ValueError):
        return None
    return got if all(math.isfinite(v) for v in got.values()) else None


def _normalizer(target: float, m: dict[str, float]) -> str:
    return (f"loudnorm=I={target:g}:TP={TRUE_PEAK:g}:LRA={LRA:g}:measured_I={m['i']:.2f}:"
            f"measured_LRA={m['lra']:.2f}:measured_TP={m['tp']:.2f}:"
            f"measured_thresh={m['thresh']:.2f}:offset={m['offset']:.2f}:linear=true")


# ------------------------------------------------------------------ 混音

def _gain_db(narration: Path | None, music: Resolved, total: float, volume: float,
             timeout: float) -> float:
    """音樂要加幾 dB，才會比旁白小 `volume` 倍（響度對響度）。"""
    rel = 20 * math.log10(volume)
    if narration is None:
        return max(GAIN_LIMITS[0], min(GAIN_LIMITS[1], rel))   # 沒有旁白可比，後面反正會整體標準化
    voice = measure(narration, timeout=timeout)
    song = measure(music.path, start=music.offset, length=total, timeout=timeout)
    if voice is None or song is None:
        return rel
    return max(GAIN_LIMITS[0], min(GAIN_LIMITS[1], voice["i"] - song["i"] + rel))


def _build_mix(narration: Path | None, music: Resolved, settings: dict[str, Any], total: float,
               gain: float, looped: bool, spec: OutputSpec, dest: Path, timeout: float) -> bool:
    """旁白＋音樂 → dest（48kHz 立體聲浮點 WAV）。回有沒有做 ducking。"""
    rate = spec.audio_rate
    fmt = f"aresample={rate},aformat=sample_fmts=fltp:channel_layouts=stereo"
    fade_out = min(FADE_OUT, total / 3)
    fade_in = min(FADE_IN, total / 6)
    chain = [fmt]
    if music.offset > 0:
        chain += [f"atrim=start={music.offset:.3f}", "asetpts=PTS-STARTPTS"]
    if looped:
        chain.append("aloop=loop=-1:size=2147483647")
    chain += [f"atrim=end={total:.3f}", f"volume={gain:.2f}dB",
              f"afade=t=in:st=0:d={fade_in:.2f}",
              f"afade=t=out:st={max(0.0, total - fade_out):.3f}:d={fade_out:.2f}"]
    ducked = narration is not None and settings["duck"]
    args: list[str] = []
    if narration is None:
        args += ["-i", str(music.path)]
        graph = f"[0:a]{','.join(chain)}[mix]"
    else:
        args += ["-i", str(narration), "-i", str(music.path)]
        voice = f"[0:a]{fmt}"
        if ducked:
            graph = (f"{voice},asplit=2[v][sc];[1:a]{','.join(chain)}[m];"
                     f"[m][sc]{DUCK}[md];[v][md]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            graph = (f"{voice}[v];[1:a]{','.join(chain)}[m];"
                     f"[v][m]amix=inputs=2:duration=first:normalize=0[mix]")
    tmp = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
    try:
        ffmpeg.run([*args, "-filter_complex", graph, "-map", "[mix]", "-t", f"{total:.3f}",
                    "-c:a", "pcm_f32le", str(tmp)], timeout=timeout, duration=total)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return ducked


def plan(narration: Path | None, music: Resolved | None, music_cfg: dict[str, Any] | None,
         total: float, spec: OutputSpec, cfg: Config, render_dir: Path, *,
         timeout: float = 900.0) -> AudioPlan:
    """決定成片的聲音：沒有旁白也沒有音樂 → 無聲；其餘混好、量響度、備好標準化濾鏡。

    `music_cfg` 是 `music_settings()` 的結果；`music` 是已經取回的音樂檔。
    """
    warnings: list[str] = []
    info: dict[str, Any] = {"music": None, "loudness": None}
    source: Path | None = narration
    if music is not None and music_cfg is not None:
        available = ffmpeg.probe(music.path)["duration"] - music.offset
        if available <= 0.05:
            warnings.append(f"音樂的 start={music.offset:g} 已經超過檔案長度，沒有混入音樂")
        else:
            looped = available + 0.05 < total
            if looped:
                warnings.append(f"音樂從 {music.offset:g} 秒起只剩 {available:.1f} 秒，"
                                f"不夠 {total:.1f} 秒，已循環補長")
            gain = _gain_db(narration, music, total, music_cfg["volume"], timeout)
            render_dir.mkdir(parents=True, exist_ok=True)
            mix_path = render_dir / "mix.wav"
            ducked = _build_mix(narration, music, music_cfg, total, gain, looped, spec,
                                mix_path, timeout)
            source = mix_path
            info["music"] = {"gain_db": round(gain, 1), "ducked": ducked, "looped": looped,
                             "volume": music_cfg["volume"]}
    if source is None:
        return AudioPlan(None, "", info, tuple(warnings))
    flt = ""
    if cfg.normalize_audio:
        measured = measure(source, timeout=timeout)
        if measured is None:
            warnings.append("聲音太短或完全靜音，量不出響度，沒有做標準化")
        else:
            flt = _normalizer(cfg.target_lufs, measured)
            info["loudness"] = {"input": round(measured["i"], 1), "target": cfg.target_lufs}
    return AudioPlan(source, flt, info, tuple(warnings))
