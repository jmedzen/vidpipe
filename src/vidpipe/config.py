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

from .errors import ConfigError

ENV_PREFIX = "VIDPIPE_"
SECRET_FIELDS = {"vidgrab_token", "tts_token"}


@dataclass
class Config:
    # vidgrab：素材來源
    vidgrab_url: str = "http://127.0.0.1:8310"
    vidgrab_token: str = ""
    # audio.cpp（或任何 OpenAI 風格的 /v1/audio/speech，例如 ttsgrab）：旁白
    tts_base_url: str = "http://127.0.0.1:8301"
    tts_token: str = ""
    tts_model: str = ""          # 尚未決定；空字串代表「由伺服器預設」
    voice_ref: str = ""          # 參考音檔路徑（CosyVoice3 等需要）
    reference_text: str = ""     # 參考音檔的逐字稿
    # 輸出
    work_dir: str = "work"       # 中間產物與快取
    out_dir: str = "out"         # 成品
    record_jobs: bool = True     # 長指令在 work_dir/_jobs 留下狀態與事件（網頁看得到）
    # 字幕
    subtitle_langs: str = "zh-TW"  # zh-TW 或 zh-TW+en（雙語）
    burn_subtitles: bool = True    # 預設燒進影片；關掉就只輸出字幕檔

    def to_dict(self, mask: bool = False) -> dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        if mask:
            for key in SECRET_FIELDS:
                if out.get(key):
                    out[key] = "***"
        return out


_VALID_SUBTITLES = {"zh-TW", "zh-TW+en"}


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
    cfg.vidgrab_url = cfg.vidgrab_url.rstrip("/")
    cfg.tts_base_url = cfg.tts_base_url.rstrip("/")
    return cfg
