"""自動品質檢查。每一項:{"name", "ok", "hard", "detail"}。hard 的不過就不能出貨。"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import textcheck

LIMITS = {"short": (30.0, 120.0), "long": (300.0, 720.0)}
SIZES = {"short": (1080, 1920), "long": (1920, 1080)}


def _ffpath(cfg: dict) -> dict:
    env = dict(os.environ)
    pre = cfg["vidpipe"].get("path_prepend")
    if pre:
        env["PATH"] = os.path.expanduser(pre) + os.pathsep + env.get("PATH", "")
    return env


def _run(cfg: dict, args: list[str], timeout: float = 900) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, env=_ffpath(cfg),
                          timeout=timeout, check=False)
    return proc.stdout + proc.stderr


def probe(cfg: dict, path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
                          str(path)], capture_output=True, text=True, env=_ffpath(cfg),
                         timeout=60, check=False).stdout
    return json.loads(out or "{}")


def _srt(path: Path) -> list[tuple[float, float, str]]:
    def t(s: str) -> float:
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))
    out = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = lines[1].split(" --> ")
            out.append((t(a), t(b), "".join(lines[2:])))
    return out


def check(cfg: dict, *, video: Path, fmt: str, script: dict, vp_dir: Path, credits: dict,
          description: str, allow_unknown: bool) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, hard: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "hard": hard, "detail": detail})

    info = probe(cfg, video)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    w, h = SIZES[fmt]
    lo, hi = LIMITS[fmt]
    add("resolution", (v.get("width"), v.get("height")) == (w, h),
        f"{v.get('width')}x{v.get('height')}(要 {w}x{h})")
    add("video_codec", v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p",
        f"{v.get('codec_name')} {v.get('pix_fmt')}")
    add("fps", v.get("r_frame_rate") in ("30/1", "30000/1001", "60/1"), str(v.get("r_frame_rate")))
    add("audio_codec", a.get("codec_name") == "aac", str(a.get("codec_name")))
    add("duration", lo <= duration <= hi, f"{duration:.1f} 秒(要 {lo:g}–{hi:g})")

    # 響度與真峰值
    out = _run(cfg, ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                     "ebur128=peak=true", "-f", "null", "-"])
    tail = out[out.rfind("Summary:"):] if "Summary:" in out else ""
    m_i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    m_p = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    lufs = float(m_i.group(1)) if m_i else None
    peak = float(m_p.group(1)) if m_p else None
    target = float(cfg.get("target_lufs", -14))
    add("loudness", lufs is not None and abs(lufs - target) <= 1.5,
        f"{lufs} LUFS(目標 {target:g} ±1.5)")
    add("true_peak", peak is not None and peak <= -0.5, f"{peak} dBFS(要 ≤ -0.5)")

    # 靜音、黑畫面、凍結
    out = _run(cfg, ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-af",
                     "silencedetect=noise=-45dB:d=2.0", "-vf",
                     "blackdetect=d=0.4:pix_th=0.08,freezedetect=n=-55dB:d=2.5", "-f", "null", "-"])
    silences = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", out)]
    music = bool(cfg.get("music"))
    add("silence", not silences or music, f"{len(silences)} 段超過 2 秒的靜音" +
        (f",最長 {max(silences):.1f} 秒" if silences else ""),
        hard=bool(silences) and max(silences) > 3.5)
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", out)
    add("black_frames", not blacks, f"{len(blacks)} 段黑畫面 {blacks[:3]}")
    freezes = re.findall(r"freeze_duration: ([\d.]+)", out)
    add("frozen_video", not freezes, f"{len(freezes)} 段畫面凍結超過 2.5 秒", hard=False)

    # 字幕:與時間軸對齊、與腳本逐字相同
    tl_file, srt_file = vp_dir / "timeline.json", vp_dir / "subs.srt"
    if tl_file.is_file() and srt_file.is_file():
        tl = json.loads(tl_file.read_text(encoding="utf-8"))
        sents = [s for sc in tl["scenes"] for s in sc["sentences"]]
        subs = _srt(srt_file)
        worst = 0.0
        for s in sents:       # 每一句旁白開始時,要有一條字幕在 0.3 秒內開始
            worst = max(worst, min((abs(e[0] - s["start"]) for e in subs), default=9.0))
        add("subtitle_sync", worst < 0.3, f"旁白與字幕起點最大誤差 {worst:.3f} 秒")
        norm = lambda t: textcheck.PUNCT.sub("", t)   # noqa: E731
        spoken = norm("".join(s["text"] for s in sents))
        shown = norm("".join(e[2] for e in subs))
        wanted = norm("".join(sc.get("text", "") for sc in textcheck.flatten(script)))
        add("subtitle_text", spoken == shown == wanted,
            "字幕、旁白、腳本逐字相同" if spoken == shown == wanted else
            f"不一致:腳本 {len(wanted)} 字、旁白 {len(spoken)} 字、字幕 {len(shown)} 字")
        apart = all(subs[i][1] <= subs[i + 1][0] + 0.001 for i in range(len(subs) - 1))
        add("subtitle_overlap", apart, "字幕彼此不重疊")
        add("simplified_chars", not textcheck.simplified_found(shown),
            "".join(textcheck.simplified_found(shown)) or "沒有簡體字")
        gaps = [sc["duration"] for sc in tl["scenes"]]
        add("scene_length", max(gaps) <= 16, f"最長的場景 {max(gaps):.1f} 秒(畫面不換超過 16 秒)",
            hard=False)
    else:
        add("subtitle_sync", False, "找不到 timeline.json 或 subs.srt")

    # 授權
    unclear = credits.get("unclear") or []
    add("license_unclear", not unclear, f"授權不明的素材 {len(unclear)} 個:{unclear[:4]}",
        hard=not allow_unknown)
    missing = [who for who in credits.get("attribution_required") or []
               if who.split(":")[-1] not in description]
    add("attribution_in_description", not missing, f"說明欄缺少標註:{missing}" if missing
        else "需要標註的素材都在說明欄")
    sources = json.loads((vp_dir / "sources.json").read_text(encoding="utf-8")) \
        if (vp_dir / "sources.json").is_file() else {"scenes": []}
    ids = [((s.get("source") or {}).get("asset_id") or (s.get("source") or {}).get("path"),
            float(s.get("offset") or 0)) for s in sources["scenes"]]
    # 相鄰鏡頭可以取自同一支素材的不同段落;起點差不到 4 秒才算「畫面沒換」
    adjacent = sum(1 for i in range(len(ids) - 1) if ids[i][0] and ids[i][0] == ids[i + 1][0]
                   and abs(ids[i][1] - ids[i + 1][1]) < 4)
    add("adjacent_same_footage", adjacent == 0, f"{adjacent} 處相鄰鏡頭畫面幾乎相同", hard=False)
    looped = [s["scene"] for s in sources["scenes"] if s.get("looped")]
    add("looped_footage", not looped, f"循環補長的場景:{looped}", hard=False)
    size = video.stat().st_size
    add("file_size", size < 2 * 1024 ** 3, f"{size / 1e6:.1f} MB")

    hard_fail = [c["name"] for c in checks if c["hard"] and not c["ok"]]
    soft_fail = [c["name"] for c in checks if not c["hard"] and not c["ok"]]
    return {"ok": not hard_fail, "hard_fail": hard_fail, "soft_fail": soft_fail,
            "duration": duration, "loudness": lufs, "checks": checks}


def contact_sheet(cfg: dict, video: Path, dest: Path, every: float, cols: int = 6) -> None:
    """抽影格排成一張圖,給人(或代理)檢視畫面與字幕。"""
    import math
    info = probe(cfg, video)
    frames = float(info.get("format", {}).get("duration", 60) or 60) / every
    rows = max(1, math.ceil(frames / cols))
    _run(cfg, ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(video), "-vf",
               f"fps=1/{every},scale=240:-1,tile={cols}x{rows}", "-frames:v", "1", str(dest)])
