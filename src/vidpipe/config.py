"""設定：環境變數 > 設定檔 > 預設值。

設定檔是 JSON（預設 ./vidpipe.json，可用 VIDPIPE_CONFIG 指定）。
token 這類祕密建議只放環境變數；`vidpipe config` 顯示時一律遮蔽。

TTS 的模型、音色參考音檔、逐字稿都是設定值，不寫死——換模型不必改程式。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError

ENV_PREFIX = "VIDPIPE_"
SECRET_FIELDS = {"vidgrab_token", "tts_token"}


@dataclass
class Config:
    # vidgrab：素材來源
    vidgrab_url: str = "http://127.0.0.1:8310"
    vidgrab_token: str = ""
    # 瀏覽器開 vidgrab 網頁用的位址；空 = 從 vidgrab_url 推（見 vidgrab_open_url）。
    # Docker／NAS：vidgrab_url 常是容器內位址（http://vidgrab:8310），瀏覽器打不開，要在這裡填對外位址。
    vidgrab_public_url: str = ""
    # audio.cpp（或任何 OpenAI 風格的 /v1/audio/speech，例如 ttsgrab）：旁白
    tts_base_url: str = "http://127.0.0.1:8301"
    tts_token: str = ""
    tts_model: str = ""          # 尚未決定；空字串代表「由伺服器預設」
    tts_concurrency: int = 1     # 同時送幾句去合成；本機單一模型通常 1 最穩
    voice_ref: str = ""          # 參考音檔路徑（CosyVoice3 等需要）
    reference_text: str = ""     # 參考音檔的逐字稿
    # 輸出
    work_dir: str = "work"       # 中間產物與快取
    out_dir: str = "out"         # 成品
    record_jobs: bool = True     # 長指令在 work_dir/_jobs 留下狀態與事件（網頁看得到）
    # 字幕
    subtitle_langs: str = "zh-TW"  # zh-TW 或 zh-TW+en（雙語）
    burn_subtitles: bool = True    # 預設燒進影片；關掉就只輸出字幕檔
    subtitle_font: str = "Noto Sans CJK TC"  # 燒字幕用的字體；要是系統裝得到的繁中字體
    # 聲音
    normalize_audio: bool = True   # 成片的響度標準化到 target_lufs（TTS 的音量常常偏小）
    target_lufs: float = -14.0     # 整合響度（LUFS）；Shorts／串流平台大約都落在 -14

    def to_dict(self, mask: bool = False) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        if mask:
            for key in SECRET_FIELDS:
                if out.get(key):
                    out[key] = "***"
        return out


_VALID_SUBTITLES = {"zh-TW", "zh-TW+en"}


def _web_url(name: str, value: str) -> str:
    """瀏覽器要開的位址：只收 http(s)、要有主機、不含帳密；回傳去掉結尾斜線的結果。"""
    parts = urlsplit(value)
    try:
        host, _ = parts.hostname, parts.port
    except ValueError:
        host = None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise ConfigError(f"{name} 要是 http(s)://主機[:埠] 的網址，"
                          f"不能含帳號密碼（收到 {value!r}）",
                          hint="例如 http://192.168.1.10:8310 或 https://vidgrab.example.com")
    return value.rstrip("/")


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def vidgrab_open_url(cfg: Config) -> dict[str, Any]:
    """瀏覽器開 vidgrab 網頁的位址。回傳 {url, source, hint}；url 為 None 代表推不出來。

    source：`public`（vidgrab_public_url 明設）、`derived`（從 vidgrab_url 推的）、`none`。
    推導規則：本機位址原樣用；`host.docker.internal` 當成「主機本人」改成 localhost；
    沒有點的單一名稱（compose 服務名如 `vidgrab`）瀏覽器解析不到，不猜，叫使用者設 public url。
    """
    if cfg.vidgrab_public_url:
        return {"url": cfg.vidgrab_public_url, "source": "public", "hint": ""}
    parts = urlsplit(cfg.vidgrab_url)
    host = (parts.hostname or "").lower()
    if host == "host.docker.internal":
        netloc = "localhost" + (f":{parts.port}" if parts.port else "")
        return {"url": urlunsplit((parts.scheme, netloc, parts.path, "", "")).rstrip("/"),
                "source": "derived", "hint": ""}
    internal = host not in _LOOPBACK and "." not in host and ":" not in host
    if not host or internal or parts.username or parts.password:
        return {"url": None, "source": "none",
                "hint": f"vidgrab_url（{_redact(cfg.vidgrab_url)}）是容器內的位址，瀏覽器打不開；"
                        "設 VIDPIPE_VIDGRAB_PUBLIC_URL 成瀏覽器連得到的網址"}
    return {"url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/"),
            "source": "derived", "hint": ""}


def _redact(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def config_path() -> Path:
    return Path(os.environ.get("VIDPIPE_CONFIG") or "vidpipe.json").expanduser()


def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
        raise ConfigError(f"{name} 要是 true/false（收到 {value!r}）")
    if isinstance(default, float):
        try:
            return float(str(value).strip())
        except ValueError:
            raise ConfigError(f"{name} 要是數字（收到 {value!r}）") from None
    if isinstance(default, int):
        try:
            return int(str(value).strip())
        except ValueError:
            raise ConfigError(f"{name} 要是整數（收到 {value!r}）") from None
    return str(value)


def load(path: Path | None = None) -> Config:
    cfg = Config()
    defaults = Config()
    file = path or config_path()
    data: dict[str, Any] = {}
    if file.exists():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"設定檔讀不了：{file}（{exc}）",
                              hint="確認是合法的 JSON 物件") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"設定檔必須是 JSON 物件：{file}")
    names = {f.name for f in fields(cfg)}
    unknown = sorted(set(data) - names)
    if unknown:
        raise ConfigError(f"設定檔有不認得的項目：{', '.join(unknown)}",
                          hint=f"可用項目：{', '.join(sorted(names))}")
    for name in names:
        value: Any = data.get(name, getattr(defaults, name))
        env = os.environ.get(ENV_PREFIX + name.upper())
        if env is not None:
            value = env
        setattr(cfg, name, _coerce(name, value, getattr(defaults, name)))
    if cfg.subtitle_langs not in _VALID_SUBTITLES:
        raise ConfigError(f"subtitle_langs 不認得：{cfg.subtitle_langs}",
                          hint="可用 zh-TW 或 zh-TW+en")
    if not cfg.subtitle_font.strip() or any(c in cfg.subtitle_font for c in ",\n\r"):
        raise ConfigError(f"subtitle_font 不能是空的，也不能含逗號或換行"
                          f"（收到 {cfg.subtitle_font!r}）")
    if not -30.0 <= cfg.target_lufs <= -5.0:
        raise ConfigError(f"target_lufs 要在 -30 到 -5 之間（收到 {cfg.target_lufs:g}）",
                          hint="Shorts 常用 -14；比 -16 小聲的片會被平台判成太小聲")
    if not 1 <= cfg.tts_concurrency <= 16:
        raise ConfigError(f"tts_concurrency 要在 1–16（收到 {cfg.tts_concurrency}）")
    cfg.vidgrab_url = cfg.vidgrab_url.rstrip("/")
    cfg.tts_base_url = cfg.tts_base_url.rstrip("/")
    if cfg.vidgrab_public_url.strip():
        cfg.vidgrab_public_url = _web_url("vidgrab_public_url", cfg.vidgrab_public_url.strip())
    else:
        cfg.vidgrab_public_url = ""
    return cfg
