"""`vidpipe doctor`：一次檢查整條流水線需要的東西。

每項檢查回 {name, ok, required, detail, hint}。`required=False` 的項目失敗只是警告，
不影響整體 `ok`（例如 CJK 字型、音色參考路徑）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .clients import TTSClient, VidgrabClient
from .config import Config
from .errors import UpstreamError, VidpipeError

Check = dict[str, Any]

CHECK_NAMES = ("ffmpeg", "ffprobe", "ffmpeg_subtitles", "cjk_font", "dirs",
               "vidgrab", "tts", "voice_ref")

_FONT_DIRS = (
    "/usr/share/fonts/opentype/noto", "/usr/share/fonts/truetype/noto",
    "/System/Library/Fonts", "/Library/Fonts", "/usr/share/fonts",
)
_CJK_MARKERS = ("cjk", "pingfang", "heiti", "hiragino", "songti", "wenquanyi", "sourcehan")


def _check(name: str, ok: bool, detail: str = "", hint: str = "", required: bool = True) -> Check:
    return {"name": name, "ok": ok, "required": required, "detail": detail, "hint": hint}


def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def check_binary(name: str) -> Check:
    path = shutil.which(name)
    if not path:
        return _check(name, False, "找不到", "安裝 ffmpeg（macOS：brew install ffmpeg）")
    try:
        first = (_run([name, "-version"]).stdout or "").splitlines()[:1]
    except (OSError, subprocess.SubprocessError):
        first = []
    return _check(name, True, first[0] if first else path)


def check_subtitles_filter(cfg: Config) -> Check:
    """燒字幕要 ffmpeg 編進 libass（`subtitles` 濾鏡）。只有要燒字幕時才是必要的。"""
    required = cfg.burn_subtitles
    if not shutil.which("ffmpeg"):
        return _check("ffmpeg_subtitles", False, "沒有 ffmpeg", required=required)
    try:
        out = _run(["ffmpeg", "-hide_banner", "-filters"]).stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("ffmpeg_subtitles", False, f"無法列出濾鏡：{type(exc).__name__}",
                      required=required)
    has = any(line.split()[1:2] == ["subtitles"] for line in out.splitlines())
    if has:
        return _check("ffmpeg_subtitles", True, "有 subtitles 濾鏡（libass）", required=required)
    return _check("ffmpeg_subtitles", False, "這個 ffmpeg 沒有 subtitles 濾鏡",
                  "換一個含 libass 的 ffmpeg（Docker 映像已內建）；"
                  "或把 burn_subtitles 關掉，只輸出字幕檔", required=required)


def check_cjk_font() -> Check:
    """找得到 CJK 字型嗎？找不到字幕會變方塊。只是警告。"""
    fc = shutil.which("fc-list")
    if fc:
        try:
            out = (_run([fc, ":lang=zh", "family"]).stdout or "").strip()
            if out:
                return _check("cjk_font", True, out.splitlines()[0], required=False)
        except (OSError, subprocess.SubprocessError):
            pass
    for root in _FONT_DIRS:
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            for f in base.rglob("*"):
                if any(m in f.name.lower() for m in _CJK_MARKERS):
                    return _check("cjk_font", True, str(f), required=False)
        except OSError:
            continue
    return _check("cjk_font", False, "找不到 CJK 字型",
                  "安裝 Noto Sans CJK（Linux：fonts-noto-cjk；macOS 內建 PingFang）",
                  required=False)


def check_dirs(cfg: Config) -> Check:
    bad = []
    for label, d in (("work_dir", cfg.work_dir), ("out_dir", cfg.out_dir)):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            if not os.access(d, os.W_OK):
                bad.append(f"{label}={d} 不可寫")
        except OSError as exc:
            bad.append(f"{label}={d}（{exc.strerror or type(exc).__name__}）")
    if bad:
        return _check("dirs", False, "；".join(bad),
                      "換一個可寫的目錄（VIDPIPE_WORK_DIR / VIDPIPE_OUT_DIR）")
    return _check("dirs", True, f"work={cfg.work_dir} out={cfg.out_dir}")


def check_vidgrab(client: VidgrabClient) -> Check:
    try:
        info = client.version()
    except VidpipeError as exc:
        return _check("vidgrab", False, exc.message, exc.hint)
    version = info.get("full") or info.get("version") or "?"
    try:
        client.check_auth()
    except UpstreamError as exc:
        if exc.upstream_code == "unauthorized" or "401" in exc.message or "403" in exc.message:
            return _check("vidgrab", False, f"連得到（{version}）但 token 不對或沒設",
                          "設 VIDPIPE_VIDGRAB_TOKEN（vidgrab 的 VIDGRAB_API_TOKEN）")
        return _check("vidgrab", False, exc.message, exc.hint)
    return _check("vidgrab", True, f"vidgrab {version}")


def check_tts(client: TTSClient, wanted_model: str = "") -> Check:
    try:
        client.health()
    except VidpipeError as exc:
        return _check("tts", False, exc.message, exc.hint)
    try:
        models = client.models()
    except UpstreamError as exc:
        # 沒有 /v1/models 的 OpenAI 相容服務（例如部分閘道）：健康就算過
        if "404" in exc.message:
            return _check("tts", True, "服務健康（沒有 /v1/models，略過模型檢查）")
        return _check("tts", False, exc.message, exc.hint)
    ids = [str(m.get("id") or m.get("name") or "") for m in models]
    if not ids:
        return _check("tts", False, "服務健康，但沒有已載入的模型",
                      "先載入模型：audio.cpp 的 WebUI 或 POST /v1/models/load（重啟後要重載）")
    if wanted_model and wanted_model not in ids:
        return _check("tts", False, f"沒有載入 {wanted_model}（已載入：{', '.join(ids)}）",
                      "改 VIDPIPE_TTS_MODEL，或先載入那個模型")
    return _check("tts", True, f"已載入：{', '.join(ids)}")


def check_voice_ref(cfg: Config) -> Check:
    """音色參考音檔。路徑是 TTS 伺服器那台機器上的，這裡只能在同一台機器時驗證。"""
    if not cfg.voice_ref:
        return _check("voice_ref", True, "未設定（CosyVoice3 之類的模型需要）", required=False)
    if Path(cfg.voice_ref).expanduser().is_file():
        extra = "" if cfg.reference_text else "；但還沒設 reference_text（逐字稿）"
        return _check("voice_ref", True, f"{cfg.voice_ref}{extra}", required=False)
    return _check("voice_ref", False, f"本機找不到 {cfg.voice_ref}",
                  "路徑必須是 TTS 伺服器那台機器上的；TTS 在別台就可以忽略這個警告",
                  required=False)


def run(cfg: Config, skip: Iterable[str] = (),
        vidgrab: Callable[[], VidgrabClient] | None = None,
        tts: Callable[[], TTSClient] | None = None) -> dict[str, Any]:
    skip = set(skip)
    unknown = skip - set(CHECK_NAMES)
    if unknown:
        from .errors import BadInput

        raise BadInput(f"不認得的檢查項目：{', '.join(sorted(unknown))}",
                       hint=f"可用：{', '.join(CHECK_NAMES)}")
    make_vg = vidgrab or (lambda: VidgrabClient(cfg.vidgrab_url, cfg.vidgrab_token, retries=0))
    make_tts = tts or (lambda: TTSClient(cfg.tts_base_url, cfg.tts_token, retries=0))
    plan: list[tuple[str, Callable[[], Check]]] = [
        ("ffmpeg", lambda: check_binary("ffmpeg")),
        ("ffprobe", lambda: check_binary("ffprobe")),
        ("ffmpeg_subtitles", lambda: check_subtitles_filter(cfg)),
        ("cjk_font", check_cjk_font),
        ("dirs", lambda: check_dirs(cfg)),
        ("vidgrab", lambda: check_vidgrab(make_vg())),
        ("tts", lambda: check_tts(make_tts(), cfg.tts_model)),
        ("voice_ref", lambda: check_voice_ref(cfg)),
    ]
    checks = [fn() for name, fn in plan if name not in skip]
    ok = all(c["ok"] for c in checks if c["required"])
    return {"ok": ok, "checks": checks, "skipped": sorted(skip)}
