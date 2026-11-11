"""參考音檔資料夾：掃描、補逐字稿、決定實際送給 TTS 的音色參考音檔與逐字稿。

設定 `voice_ref_dir` 指向一個放參考音檔的資料夾後：

- 每個音檔（.wav／.mp3／.flac／.m4a）旁邊的同名 `.txt` 就是它的逐字稿；
- 沒有逐字稿的，`transcribe_missing` 用 audio.cpp 的 ASR（`POST /v1/audio/transcriptions`，
  模型 `asr_model`、語言 `asr_language`）辨識，轉成台灣繁體後存成同名 `.txt`；
- `voice_ref` 可以只寫資料夾裡的檔名；`reference_text` 沒填時自動讀那個檔的 `.txt`。

路徑都是 **TTS 伺服器那台機器上的**：audio.cpp 直接讀這個路徑（ASR 與 TTS 都是）。
vidpipe 與 audio.cpp 不在同一台機器時（例如 vidpipe 在 NAS 的 Docker），vidpipe 讀不到這個資料夾，
掃描與自動補逐字稿只能在 audio.cpp 那台機器上跑（`vidpipe voices --transcribe`）。
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import jobs
from .clients.tts import TTSClient
from .config import Config
from .errors import BadInput, NotFound, VidpipeError

AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a")
MAX_FILES = 200


def transcript_path(audio: Path) -> Path:
    return audio.with_suffix(".txt")


def _dir(cfg: Config) -> Path:
    if not cfg.voice_ref_dir:
        raise BadInput("還沒設定參考音檔資料夾（voice_ref_dir）",
                       hint="在設定頁「旁白 · TTS」填資料夾路徑，或設 VIDPIPE_VOICE_REF_DIR")
    root = Path(cfg.voice_ref_dir).expanduser()
    if not root.is_dir():
        raise NotFound(f"找不到參考音檔資料夾：{root}",
                       hint="路徑是 TTS（audio.cpp）那台機器上的；"
                            "vidpipe 跟它不在同一台機器時讀不到")
    return root


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def scan(cfg: Config) -> list[dict[str, Any]]:
    """資料夾裡的音檔與它們的逐字稿（依檔名排序）。"""
    root = _dir(cfg)
    files = sorted(p for p in root.iterdir()
                   if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith("."))
    out = []
    for audio in files[:MAX_FILES]:
        txt = transcript_path(audio)
        text = _read_text(txt) if txt.is_file() else ""
        out.append({"name": audio.name, "path": str(audio), "transcript": str(txt),
                    "has_transcript": bool(text), "text": text})
    return out


_TW = None


def to_traditional(text: str) -> str:
    """ASR 常吐簡體：有裝 OpenCC 就轉成台灣繁體（含用詞，s2twp），沒裝就原樣。"""
    global _TW
    if _TW is None:
        try:
            import opencc  # type: ignore[import-not-found]

            _TW = opencc.OpenCC("s2twp")
        except Exception:  # noqa: BLE001 - 沒裝或裝的版本不同：退回原樣
            _TW = False
    return _TW.convert(text) if _TW else text


_FULLWIDTH = str.maketrans({",": "，", "?": "？", "!": "！", ":": "：", ";": "；"})


def tidy(text: str) -> str:
    """逐字稿：中文的標點轉全形、去掉多餘空白，並轉成台灣繁體。

    沒有中文字的（英文參考音檔）原樣保留，標點不動，免得英文被塞進全形逗號。"""
    text = text.strip()
    if not re.search(r"[\u4e00-\u9fff]", text):
        return re.sub(r"\s+", " ", text)
    text = to_traditional(text).translate(_FULLWIDTH)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\.", "。", text)
    return re.sub(r"\s+", "", text)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.partial")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def make_client(cfg: Config) -> TTSClient:
    return TTSClient(cfg.tts_base_url, cfg.tts_token, timeout=300.0)


def transcribe_missing(cfg: Config, *, client: TTSClient | None = None) -> dict[str, Any]:
    """沒有逐字稿的音檔用 audio.cpp 的 ASR 辨識，存成同名 `.txt`。已經有的不動。

    一個檔失敗不影響其他檔；結果逐檔列出（`existing`／`transcribed`／`failed`）。
    """
    items = scan(cfg)
    todo = [it for it in items if not it["has_transcript"]]
    if todo and not cfg.asr_model:
        raise BadInput("有參考音檔沒有逐字稿，但還沒設定 ASR 模型（asr_model）",
                       hint="先在 audio.cpp 載入一個 ASR 模型，"
                            "再把它的 id 填進設定頁的「ASR 模型」")
    client = client or make_client(cfg)
    results = []
    for n, it in enumerate(items, 1):
        if it["has_transcript"]:
            results.append({**it, "status": "existing"})
            continue
        jobs.stage("asr", f"{n}/{len(items)}　{it['name']}")
        try:
            raw = client.transcribe(it["path"], model=cfg.asr_model, language=cfg.asr_language)
            text = tidy(raw)
            if not text:
                raise VidpipeError("ASR 沒有辨識出任何文字", code="upstream_error")
            _atomic_write(Path(it["transcript"]), text)
            results.append({**it, "status": "transcribed", "text": text, "has_transcript": True})
        except OSError as exc:
            results.append({**it, "status": "failed", "error": f"寫不進逐字稿：{exc}"})
        except VidpipeError as exc:
            results.append({**it, "status": "failed", "error": exc.message})
    summary = {k: sum(1 for r in results if r["status"] == k)
               for k in ("existing", "transcribed", "failed")}
    return {"dir": str(_dir(cfg)), "items": results, **summary}


def resolve(cfg: Config) -> Config:
    """實際要送給 TTS 的音色：`voice_ref` 只寫檔名時到 `voice_ref_dir` 裡找；
    `reference_text` 沒填時讀那個音檔旁邊的 `.txt`。找不到就原樣回傳（交給 doctor／TTS 報錯）。"""
    ref = cfg.voice_ref
    if not ref:
        return cfg
    path = Path(ref).expanduser()
    if not path.is_absolute() and cfg.voice_ref_dir:
        candidate = Path(cfg.voice_ref_dir).expanduser() / ref
        if candidate.is_file():
            path = candidate
    changes: dict[str, str] = {}
    if str(path) != ref and path.is_file():
        changes["voice_ref"] = str(path)
    if not cfg.reference_text:
        txt = transcript_path(path)
        if txt.is_file():
            text = _read_text(txt)
            if text:
                changes["reference_text"] = text
    return replace(cfg, **changes) if changes else cfg
