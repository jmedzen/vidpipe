"""設定：環境變數 > 設定檔 > 預設值。

設定檔是 JSON（預設 ./vidpipe.json，可用 VIDPIPE_CONFIG 指定）。
token 這類祕密建議只放環境變數；`vidpipe config` 顯示時一律遮蔽。
空字串的環境變數視為「沒設」：docker compose 的 `${VAR:-}` 沒值時會傳空字串，
不能因此蓋掉設定檔（網頁的設定頁存的就是設定檔）。

TTS 的模型、音色參考音檔、逐字稿都是設定值，不寫死——換模型不必改程式。

每個設定項目的分組、說明、輸入型別與合法範圍集中在下面的 `SPECS`：`load()` 驗證、
網頁設定頁（`settings.py`）的表單與伺服器端驗證都用同一份，不會兩邊各說各話。
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
    voice_ref_dir: str = ""      # 參考音檔資料夾：沒有逐字稿（同名 .txt）的會自動用 ASR 補
    asr_model: str = ""          # audio.cpp 已載入的 ASR 模型 id（補逐字稿用）
    asr_language: str = "zh"     # ASR 的語言；中文結果會轉成台灣繁體
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


# ---------------------------------------------------------------- 欄位說明（單一來源）

@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    hint: str = ""


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    note: str


@dataclass(frozen=True)
class FieldSpec:
    """一個設定項目長什麼樣：網頁用它畫表單，`check_field` 用它驗證。"""

    name: str
    group: str
    kind: str                     # url｜text｜path｜textarea｜secret｜bool｜int｜float｜choice
    label: str
    help: str
    placeholder: str = ""
    unit: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    range_hint: str = ""          # 超出範圍時多給一句建議
    choices: tuple[Choice, ...] = ()
    suggestions: tuple[str, ...] = ()
    depends_on: str = ""          # 另一個欄位「有值」這個才用得到（網頁會把沒用到的調淡）
    required: bool = False        # 不能是空的
    forbidden: str = ""           # 不能出現的字元
    forbidden_label: str = ""     # 上面那些字元怎麼稱呼，錯誤訊息用

    @property
    def secret(self) -> bool:
        return self.kind == "secret"


GROUPS: tuple[Group, ...] = (
    Group("vidgrab", "素材來源 · vidgrab", "搜尋與下載素材、授權資訊"),
    Group("tts", "旁白 · TTS", "audio.cpp 或任何 OpenAI 風格的 /v1/audio/speech（例如 ttsgrab）"),
    Group("subtitles", "字幕", "語言、要不要燒進影片、用哪個字體"),
    Group("audio", "聲音", "成片的響度"),
    Group("storage", "資料夾與紀錄", "中間產物、成品放哪裡，以及工作紀錄"),
)

_SPEC_LIST: tuple[FieldSpec, ...] = (
    FieldSpec(
        "vidgrab_url", "vidgrab", "url", "vidgrab 位址",
        "vidgrab 服務的網址。Docker 裡連不到主機的 127.0.0.1，"
        "要寫 host.docker.internal 或實際 IP。",
        placeholder="http://127.0.0.1:8310", required=True),
    FieldSpec(
        "vidgrab_public_url", "vidgrab", "url", "vidgrab 網頁位址（瀏覽器用）",
        "總覽頁「開啟 vidgrab」按鈕開的網址，是「瀏覽器」連得到的位址。留空就從上面的位址推："
        "本機位址原樣用，host.docker.internal 改成 localhost。Docker／NAS 用 compose 服務名"
        "（http://vidgrab:8310）時瀏覽器打不開，要填你在瀏覽器實際輸入的網址。",
        placeholder="http://192.168.1.10:8310"),
    FieldSpec(
        "vidgrab_token", "vidgrab", "secret", "vidgrab token",
        "vidgrab 有設 VIDGRAB_API_TOKEN 才需要。存進設定檔是明文，更安全的做法是用環境變數。"),
    FieldSpec(
        "tts_base_url", "tts", "url", "TTS 位址",
        "audio.cpp（預設 8301 埠）或別的 OpenAI 風格服務，例如 ttsgrab。",
        placeholder="http://127.0.0.1:8301", required=True),
    FieldSpec(
        "tts_token", "tts", "secret", "TTS token",
        "TTS 服務有要求才需要。存進設定檔是明文，更安全的做法是用環境變數。"),
    FieldSpec(
        "tts_model", "tts", "text", "TTS 模型",
        "留空就用伺服器預設。按「測試連線」會列出已載入的模型，點一下就能填進來。",
        placeholder="（由伺服器預設）"),
    FieldSpec(
        "tts_concurrency", "tts", "int", "同時合成幾句",
        "本機單一模型通常 1 最穩；只有服務端真的能平行時，調大才會變快。",
        unit="句", min=1, max=16, step=1),
    FieldSpec(
        "voice_ref", "tts", "path", "音色參考音檔",
        "CosyVoice3 之類的模型需要。路徑是 TTS 伺服器那台機器上的，不是這台。",
        placeholder="（不用就留空）"),
    FieldSpec(
        "reference_text", "tts", "textarea", "參考音檔逐字稿",
        "參考音檔裡實際說的話，要逐字一致。沒填音色參考音檔時不會送出。"
        "留空時，會自動讀參考音檔旁邊的同名 .txt。",
        depends_on="voice_ref"),
    FieldSpec(
        "voice_ref_dir", "tts", "path", "參考音檔資料夾",
        "放參考音檔的資料夾（TTS 伺服器那台機器上的路徑）。儲存或按「掃描參考音檔」時，"
        "沒有逐字稿（同名 .txt）的音檔會用 audio.cpp 的 ASR 辨識，轉成繁體後存回同一個資料夾。"
        "設了它，上面的「音色參考音檔」可以只填檔名。",
        placeholder="（不用就留空）"),
    FieldSpec(
        "asr_model", "tts", "text", "ASR 模型",
        "補逐字稿用的 audio.cpp ASR 模型 id（要先在 audio.cpp 載入，例如 Qwen3 ASR）。"
        "按「測試連線」會列出已載入的模型。",
        placeholder="（要補逐字稿時才需要）", depends_on="voice_ref_dir"),
    FieldSpec(
        "asr_language", "tts", "text", "ASR 語言",
        "交給 ASR 的語言代碼，中文填 zh。中文結果一律轉成台灣繁體。",
        placeholder="zh", depends_on="voice_ref_dir"),
    FieldSpec(
        "subtitle_langs", "subtitles", "choice", "字幕語言",
        "每支片預設的字幕語言；manifest 的 subtitles 可以個別蓋過。",
        choices=(
            Choice("zh-TW", "繁體中文", "只顯示中文。"),
            Choice("zh-TW+en", "中英雙語",
                   "中文在上、英文在下；英文取自每句旁白的 en 欄，沒寫的句子只顯示中文。"),
        )),
    FieldSpec(
        "burn_subtitles", "subtitles", "bool", "燒進影片",
        "開：字幕直接畫在影片上（ffmpeg 要含 libass）。關：只輸出 .srt 與 .ass 字幕檔。"),
    FieldSpec(
        "subtitle_font", "subtitles", "text", "字幕字體",
        "系統裝得到的繁中字體；找不到時中文會變方塊。Docker 映像內建 Noto Sans CJK TC，"
        "Mac 通常用 PingFang TC。按「測試 ffmpeg 與字體」可以確認。",
        suggestions=("Noto Sans CJK TC", "PingFang TC", "Heiti TC", "Microsoft JhengHei"),
        required=True, forbidden=",\n\r", forbidden_label="逗號或換行"),
    FieldSpec(
        "normalize_audio", "audio", "bool", "響度標準化",
        "把成片的響度調到目標值。TTS 的音量常常偏小，開著比較保險。"),
    FieldSpec(
        "target_lufs", "audio", "float", "目標響度",
        "整合響度，Shorts 與多數串流平台大約落在 -14。比 -16 小聲的片會被平台判成太小聲。",
        unit="LUFS", min=-30.0, max=-5.0, step=0.5,
        range_hint="Shorts 常用 -14；比 -16 小聲的片會被平台判成太小聲",
        depends_on="normalize_audio"),
    FieldSpec(
        "work_dir", "storage", "path", "work 目錄",
        "中間產物與快取：旁白、素材、每支片的資料夾、工作紀錄。相對路徑是相對於 vidpipe "
        "啟動時的目錄，想讓指令列與網頁看同一份就寫絕對路徑。換掉之後，舊目錄裡的作品不會跟著搬，"
        "列表也就看不到它們。",
        required=True, forbidden="\0", forbidden_label="空字元"),
    FieldSpec(
        "out_dir", "storage", "path", "out 目錄",
        "成品放這裡（mp4、封面、字幕、素材來源清單）。",
        required=True, forbidden="\0", forbidden_label="空字元"),
    FieldSpec(
        "record_jobs", "storage", "bool", "留下工作紀錄",
        "長指令把狀態與事件寫進 work 目錄，網頁才看得到進度與失敗原因。關掉就不寫。"),
)

SPECS: dict[str, FieldSpec] = {s.name: s for s in _SPEC_LIST}
_VALID_SUBTITLES = {c.value for c in SPECS["subtitle_langs"].choices}
_DEFAULTS = Config()


def env_name(name: str) -> str:
    return ENV_PREFIX + name.upper()


def env_value(name: str) -> str | None:
    """環境變數的值；沒設、或是空字串，都回 None（空字串視為沒設，見模組說明）。"""
    raw = os.environ.get(env_name(name))
    return raw if raw else None


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
                        "到「設定」頁填「vidgrab 網頁位址」，或設 VIDPIPE_VIDGRAB_PUBLIC_URL，"
                        "內容是瀏覽器連得到的網址"}
    return {"url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/"),
            "source": "derived", "hint": ""}


def _redact(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def config_path() -> Path:
    return Path(os.environ.get("VIDPIPE_CONFIG") or "vidpipe.json").expanduser()


# ---------------------------------------------------------------- 驗證

def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        raise ConfigError(f"{name} 的值格式不對（要是單一的值，收到 {type(value).__name__}）")
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
        if isinstance(value, bool):
            raise ConfigError(f"{name} 要是數字（收到 {value!r}）")
        try:
            return float(str(value).strip())
        except ValueError:
            raise ConfigError(f"{name} 要是數字（收到 {value!r}）") from None
    if isinstance(default, int):
        if isinstance(value, bool):
            raise ConfigError(f"{name} 要是整數（收到 {value!r}）")
        try:
            return int(str(value).strip())
        except ValueError:
            raise ConfigError(f"{name} 要是整數（收到 {value!r}）") from None
    return str(value)


def _check_url(name: str, text: str) -> str:
    example = SPECS[name].placeholder or "http://127.0.0.1:8310"
    # 貼錯的網址可能夾著帳號密碼（`user:pass@host`）；錯誤訊息不能把它原樣印出來
    shown = "（含 @，不顯示）" if "@" in text else repr(text)
    if not text:
        raise ConfigError(f"{name} 不能是空的", hint=f"例如 {example}")
    if any(c.isspace() for c in text):
        raise ConfigError(f"{name} 不能含空白（收到 {shown}）")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{name} 要是 http:// 或 https:// 開頭的網址（收到 {shown}）",
                          hint=f"例如 {example}")
    if parts.username or parts.password:
        # 位址會出現在錯誤訊息裡；祕密不能跟著出現
        raise ConfigError(f"{name} 的網址裡不要放帳號密碼",
                          hint="需要驗證的話把 token 填在旁邊的 token 欄")
    if parts.query or parts.fragment:
        raise ConfigError(f"{name} 的網址不要帶 ? 或 #（收到 {shown}）")
    try:
        parts.port  # noqa: B018 - 取值只為了讓不合法的 port 丟 ValueError
    except ValueError:
        raise ConfigError(f"{name} 的 port 不對（收到 {shown}）") from None
    return text.rstrip("/")


def check_field(name: str, value: Any) -> Any:
    """驗證並轉成這個項目的型別；不合格丟 `ConfigError`（message 直接給人看）。

    `load()`、`vidpipe config`、網頁的儲存與設定頁的逐欄檢查都走這一個，規則只有一份。
    """
    spec = SPECS[name]
    coerced = _coerce(name, value, getattr(_DEFAULTS, name))
    if isinstance(coerced, str):
        coerced = coerced.strip()
        if spec.required and not coerced:
            raise ConfigError(f"{name} 不能是空的",
                              hint=f"例如 {spec.placeholder}" if spec.placeholder else "")
        bad = [c for c in spec.forbidden if c in coerced]
        if bad:
            raise ConfigError(f"{name} 不能含{spec.forbidden_label}（收到 {coerced!r}）")
    if spec.kind == "url":
        return _check_url(name, coerced) if coerced or spec.required else ""
    if spec.kind == "choice":
        if coerced not in {c.value for c in spec.choices}:
            raise ConfigError(f"{name} 不認得：{coerced}",
                              hint="可用 " + " 或 ".join(c.value for c in spec.choices))
    elif spec.kind in ("int", "float") and spec.min is not None and spec.max is not None:
        if not spec.min <= coerced <= spec.max:
            lo, hi = (f"{spec.min:g}", f"{spec.max:g}")
            if spec.kind == "int":
                raise ConfigError(f"{name} 要在 {lo}–{hi}（收到 {coerced:g}）")
            raise ConfigError(f"{name} 要在 {lo} 到 {hi} 之間（收到 {coerced:g}）",
                              hint=spec.range_hint)
    return coerced


# ---------------------------------------------------------------- 讀檔與載入

def read_file(file: Path) -> dict[str, Any]:
    """設定檔的內容（沒有檔案就是空的）。讀不了、不是 JSON 物件都丟 `ConfigError`。"""
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"設定檔讀不了：{file}（{exc}）",
                          hint="確認是合法的 JSON 物件") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"設定檔必須是 JSON 物件：{file}")
    return data


def load(path: Path | None = None) -> Config:
    cfg = Config()
    file = path or config_path()
    data = read_file(file)
    names = {f.name for f in fields(cfg)}
    unknown = sorted(set(data) - names)
    if unknown:
        raise ConfigError(f"設定檔有不認得的項目：{', '.join(unknown)}",
                          hint=f"可用項目：{', '.join(sorted(names))}")
    for name in names:
        value: Any = data.get(name, getattr(_DEFAULTS, name))
        env = env_value(name)
        origin = f"值來自設定檔 {file}"
        if env is not None:
            value, origin = env, f"值來自環境變數 {env_name(name)}"
        try:
            setattr(cfg, name, check_field(name, value))
        except ConfigError as exc:
            raise ConfigError(exc.message,
                              hint="；".join(x for x in (exc.hint, origin) if x)) from exc
    return cfg
