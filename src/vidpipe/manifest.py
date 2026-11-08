"""Shorts manifest（`vidpipe.manifest/1`）：宣告式地描述一支片。

agent 寫（或之後由 `plan` 產生）一份 JSON，`narrate`、`render` 都只讀它。同一份 manifest
重跑得到同一支片；中間產物以內容雜湊快取。

    {
      "schema": "vidpipe.manifest/1",
      "id": "taipei-night-01",
      "title": "台北夜市",                                  // 選填
      "output": {"aspect": "9:16", "fps": 30},             // 選填；預設 9:16、30fps
      "voice": {"model": "", "voice_ref": "", "reference_text": ""},   // 選填，蓋過設定
      "timing": {"lead": 0.15, "gap": 0.25, "tail": 0.45}, // 選填，秒
      "scenes": [
        {"id": "s1", "text": "台北的夜晚，從一碗熱湯開始。",
         "clip": {"asset_id": "pixabay:123", "start": 3.0},  // 或 {"path": "...", "start": 0}
         "fit": "blur_pad", "focus": "0.5,0.5", "mute_source": true},
        {"id": "s2", "sentences": [{"text": "夜市很熱鬧。", "en": "The market is lively."}]},
        {"id": "s3", "min_duration": 2.0}                  // 沒有旁白的場景要給最短長度
      ],
      "subtitles": {"langs": "zh-TW", "burn_in": true, "style": "bold-bottom"},
      "music": {"path": "/path/bgm.mp3", "start": 0, "volume": 0.3, "duck_under_voice": true}
    }

一個 scene 的旁白可以給 `text`（自動分句）或 `sentences`（明確列出每一句，雙語字幕要配對英文時用）。
不認得的欄位一律報錯（多半是打錯字），錯誤訊息帶路徑，例如 `scenes[2].text`。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import text as text_mod
from .config import Config
from .errors import BadInput, NotFound
from .video import ASPECTS, FITS, OutputSpec, parse_focus

SCHEMA = "vidpipe.manifest/1"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SUBTITLE_LANGS = ("zh-TW", "zh-TW+en")
SUBTITLE_STYLES = ("bold-bottom",)


@dataclass(frozen=True)
class Timing:
    lead: float = 0.15   # 場景開頭到第一句旁白
    gap: float = 0.25    # 同一場景內句與句之間
    tail: float = 0.45   # 最後一句到場景結束


@dataclass(frozen=True)
class Sentence:
    text: str
    en: str = ""


@dataclass(frozen=True)
class Scene:
    id: str
    sentences: tuple[Sentence, ...]
    min_duration: float = 0.0
    clip: dict[str, Any] | None = None
    fit: str = "blur_pad"
    focus: tuple[float, float] = (0.5, 0.5)
    mute_source: bool = True


@dataclass(frozen=True)
class Subtitles:
    langs: str = "zh-TW"
    burn_in: bool = True
    style: str = "bold-bottom"


@dataclass(frozen=True)
class Manifest:
    id: str
    scenes: tuple[Scene, ...]
    title: str = ""
    output: OutputSpec = field(default_factory=OutputSpec)
    aspect: str = "9:16"
    voice: dict[str, str] = field(default_factory=dict)
    timing: Timing = field(default_factory=Timing)
    subtitles: Subtitles = field(default_factory=Subtitles)
    music: dict[str, Any] | None = None


# ------------------------------------------------------------------ 欄位檢查小工具

def _fail(path: str, message: str, hint: str = "") -> BadInput:
    return BadInput(f"manifest {path}：{message}", hint=hint)


def _obj(value: Any, path: str, allowed: set[str],
         required: frozenset[str] | set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(path, "要是物件")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail(path, f"不認得的欄位 {', '.join(unknown)}",
                    f"可用欄位：{', '.join(sorted(allowed))}")
    missing = sorted(required - set(value))
    if missing:
        raise _fail(path, f"缺少欄位 {', '.join(missing)}")
    return value


def _str(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _fail(path, "要是字串")
    if not allow_empty and not value.strip():
        raise _fail(path, "不能是空的")
    return value.strip()


def _num(value: Any, path: str, *, lo: float = 0.0, hi: float = 3600.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(path, "要是數字")
    if not lo <= value <= hi:
        raise _fail(path, f"要在 {lo:g}–{hi:g} 之間（收到 {value}）")
    return float(value)


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, "要是 true 或 false")
    return value


def _choice(value: Any, path: str, options: tuple[str, ...]) -> str:
    if value not in options:
        raise _fail(path, f"不認得 {value!r}", f"可用：{', '.join(options)}")
    return str(value)


# ------------------------------------------------------------------ 解析

def _parse_scene(raw: Any, i: int, *, max_chars: int) -> Scene:
    path = f"scenes[{i}]"
    data = _obj(raw, path, {"id", "text", "sentences", "min_duration", "clip", "fit", "focus",
                            "mute_source"}, {"id"})
    sid = _str(data["id"], f"{path}.id")
    if not ID_RE.match(sid):
        raise _fail(f"{path}.id", f"{sid!r} 不合格式", "小寫英數、底線、連字號，最長 64 字")
    if "text" in data and "sentences" in data:
        raise _fail(path, "text 與 sentences 只能給一個",
                    "要配對英文字幕就用 sentences，否則用 text")
    sentences: list[Sentence] = []
    if "text" in data:
        for s in text_mod.split_sentences(_str(data["text"], f"{path}.text"), max_chars):
            sentences.append(Sentence(s))
        if not sentences:
            raise _fail(f"{path}.text", "沒有可以唸的字", "只有標點的文字無法合成")
    elif "sentences" in data:
        items = data["sentences"]
        if not isinstance(items, list) or not items:
            raise _fail(f"{path}.sentences", "要是非空的清單")
        for j, item in enumerate(items):
            p = f"{path}.sentences[{j}]"
            obj = _obj(item, p, {"text", "en"}, {"text"})
            line = _str(obj["text"], f"{p}.text")
            if not text_mod.has_speech(line):
                raise _fail(f"{p}.text", "沒有可以唸的字")
            sentences.append(Sentence(line, _str(obj.get("en", ""), f"{p}.en", allow_empty=True)))
    min_duration = _num(data["min_duration"], f"{path}.min_duration", hi=120) \
        if "min_duration" in data else 0.0
    if not sentences and min_duration <= 0:
        raise _fail(path, "沒有旁白的場景要給 min_duration（秒）",
                    "例：{\"id\": \"intro\", \"min_duration\": 2.0}")
    clip = None
    if "clip" in data:
        c = _obj(data["clip"], f"{path}.clip", {"asset_id", "path", "start"})
        if bool("asset_id" in c) == bool("path" in c):
            raise _fail(f"{path}.clip", "asset_id 與 path 要二選一",
                        "asset_id 是 vidgrab 的素材 id；path 是本機檔案")
        clip = {k: (_str(v, f"{path}.clip.{k}") if k != "start"
                    else _num(v, f"{path}.clip.start", hi=86400)) for k, v in c.items()}
    fit = _choice(data.get("fit", "blur_pad"), f"{path}.fit", FITS)
    try:
        focus = parse_focus(data.get("focus"))
    except BadInput as exc:
        raise _fail(f"{path}.focus", exc.message, exc.hint) from None
    mute = _bool(data["mute_source"], f"{path}.mute_source") if "mute_source" in data else True
    return Scene(sid, tuple(sentences), min_duration, clip, fit, focus, mute)


def parse(data: Any, *, subtitle_langs: str = "zh-TW", burn_in: bool = True,
          max_chars: int = text_mod.DEFAULT_MAX_CHARS) -> Manifest:
    """dict → Manifest。`subtitle_langs`、`burn_in` 是 manifest 沒寫時的預設（來自設定）。"""
    top = _obj(data, "（最上層）", {"schema", "id", "title", "output", "voice", "timing", "scenes",
                                   "subtitles", "music"}, {"schema", "id", "scenes"})
    if top["schema"] != SCHEMA:
        raise _fail("schema", f"要是 {SCHEMA!r}（收到 {top['schema']!r}）")
    mid = _str(top["id"], "id")
    if not ID_RE.match(mid):
        raise _fail("id", f"{mid!r} 不合格式",
                    "小寫英數、底線、連字號，最長 64 字；它會當資料夾名稱")

    out = _obj(top.get("output", {}), "output", {"aspect", "fps"})
    aspect = _choice(out.get("aspect", "9:16"), "output.aspect", tuple(ASPECTS))
    fps = int(_num(out.get("fps", 30), "output.fps", lo=1, hi=120))
    spec = OutputSpec.for_aspect(aspect, fps=fps)

    voice_raw = _obj(top.get("voice", {}), "voice", {"model", "voice_ref", "reference_text"})
    voice = {k: _str(v, f"voice.{k}", allow_empty=True) for k, v in voice_raw.items()}

    t = _obj(top.get("timing", {}), "timing", {"lead", "gap", "tail"})
    defaults = Timing()
    timing = Timing(*(_num(t.get(k, getattr(defaults, k)), f"timing.{k}", hi=10)
                      for k in ("lead", "gap", "tail")))

    s = _obj(top.get("subtitles", {}), "subtitles", {"langs", "burn_in", "style"})
    subtitles = Subtitles(
        langs=_choice(s.get("langs", subtitle_langs), "subtitles.langs", SUBTITLE_LANGS),
        burn_in=_bool(s["burn_in"], "subtitles.burn_in") if "burn_in" in s else burn_in,
        style=_choice(s.get("style", "bold-bottom"), "subtitles.style", SUBTITLE_STYLES))

    music = None
    if "music" in top and top["music"] is not None:
        m = _obj(top["music"], "music",
                 {"path", "asset_id", "start", "volume", "duck_under_voice"})
        if bool("path" in m) == bool("asset_id" in m):
            raise _fail("music", "path 與 asset_id 要二選一")
        music = {k: (_str(v, f"music.{k}") if k in ("path", "asset_id")
                     else _num(v, f"music.{k}", hi=2 if k == "volume" else 36000)
                     if k in ("volume", "start")
                     else _bool(v, "music.duck_under_voice")) for k, v in m.items()}

    raw_scenes = top["scenes"]
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise _fail("scenes", "要是非空的清單")
    scenes = tuple(_parse_scene(r, i, max_chars=max_chars) for i, r in enumerate(raw_scenes))
    seen: set[str] = set()
    for i, sc in enumerate(scenes):
        if sc.id in seen:
            raise _fail(f"scenes[{i}].id", f"{sc.id!r} 重複了")
        seen.add(sc.id)
    title = _str(top.get("title", ""), "title", allow_empty=True)
    return Manifest(mid, scenes, title, spec, aspect, voice, timing, subtitles, music)


def read_json(path: str | Path) -> Any:
    """讀 manifest 檔成原始的 JSON（還沒驗證）。"""
    p = Path(path)
    if not p.is_file():
        raise NotFound(f"找不到 manifest：{p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput(f"manifest 不是合法的 JSON：{p.name}（{exc}）") from exc


def load(path: str | Path, **kw: Any) -> Manifest:
    return parse(read_json(path), **kw)


_VOICE_FIELDS = {"model": "tts_model", "voice_ref": "voice_ref", "reference_text": "reference_text"}


def apply_voice(manifest: Manifest, cfg: Config) -> Config:
    """manifest 的 `voice` 蓋過設定檔；寫了就算（空字串 = 明確要伺服器預設）。"""
    return replace(cfg, **{_VOICE_FIELDS[k]: v for k, v in manifest.voice.items()})
