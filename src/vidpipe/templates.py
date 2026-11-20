"""排版模板（`vidpipe.template/1`）：把一支 Short 的版面存成有名字的 JSON，出片時選一個用。

模板只管「長什麼樣」，不管內容：

    {
      "schema": "vidpipe.template/1",
      "name": "classic",                       // 小寫英數、底線、連字號；也是檔名
      "label": "經典",                          // 選填：網頁上顯示的名字
      "description": "黃色大標題在上、白字幕在下",  // 選填
      "aspect": "9:16",                         // 9:16／1:1／16:9
      "fit": "crop",                            // 素材怎麼放進畫面：crop／blur_pad／letterbox
      "subtitles": {"size": "large", "color": "white", "en_color": "paleyellow",
                    "margin": null, "box": false, "font": ""},
      "title":  {"show": true, "position": "top", "size": "auto", "color": "yellow"},
      "series": {"show": true, "position": "bottom", "size": "small", "color": "white"},
      "music":  {"volume": 0.22}
    }

除了 `schema` 與 `name` 都是選填；沒寫的欄位用 `classic`（＝原本寫死的版面）的值，
所以不指定模板時成片跟以前一樣。`title.size` 的 `auto`：直式 xlarge、橫式 medium。
`subtitles.margin` 是字幕離底邊佔畫面高度的比例，null＝直式 0.2、橫式 0.08。

模板放兩個地方：內建的在套件裡（`data/templates/`，不能改也不能刪），自己存的在
`template_dir`（設定，預設 `./templates`）。指定模板時可以寫名稱，也可以直接寫 .json 檔的路徑。
套用模板＝組出 manifest 的 `output.aspect`、`scenes[].fit`、`subtitles`、`overlays`、
`music.volume`（見 `manifest_parts`）；manifest 本身不記模板名稱，所以之後改模板不會讓舊片變樣。
"""

from __future__ import annotations

import copy
import json
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any

from .colors import COLORS, is_color
from .config import Config
from .errors import BadInput, NotFound
from .video import ASPECTS, FITS

SCHEMA = "vidpipe.template/1"
DEFAULT = "classic"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SUBTITLE_SIZES = ("normal", "large", "xlarge")
OVERLAY_SIZES = ("xlarge", "large", "medium", "small")
TITLE_SIZES = ("auto", *OVERLAY_SIZES)
POSITIONS = ("top", "bottom")
MAX_MARGIN = 0.45
MAX_LABEL = 40
MAX_DESCRIPTION = 200

# classic：原本 `quick.build` 寫死的版面。改這裡會改到「沒指定模板」的成片。
BASE: dict[str, Any] = {
    "aspect": "9:16",
    "fit": "crop",
    "subtitles": {"size": "large", "color": "white", "en_color": "paleyellow",
                  "margin": None, "box": False, "font": ""},
    "title": {"show": True, "position": "top", "size": "auto", "color": "yellow"},
    "series": {"show": True, "position": "bottom", "size": "small", "color": "white"},
    "music": {"volume": 0.22},
}
TOP_KEYS = {"schema", "name", "label", "description", *BASE}


def _fail(where: str, path: str, message: str, hint: str = "") -> BadInput:
    return BadInput(f"模板{where} {path}：{message}", hint=hint)


# ------------------------------------------------------------------ 驗證

def parse(doc: Any, *, where: str = "") -> dict[str, Any]:
    """驗證一份模板，回補齊所有欄位的 dict（沒寫的用 classic 的值）。

    `where` 放在錯誤訊息裡，例如「 my.json」。不認得的欄位一律報錯（多半是打錯字）。
    """
    if not isinstance(doc, dict):
        raise _fail(where, "（最上層）", "要是物件")
    unknown = sorted(set(doc) - TOP_KEYS)
    if unknown:
        raise _fail(where, "（最上層）", f"不認得的欄位 {', '.join(unknown)}",
                    f"可用欄位：{', '.join(sorted(TOP_KEYS))}")
    if doc.get("schema") != SCHEMA:
        raise _fail(where, "schema", f"要是 {SCHEMA!r}（收到 {doc.get('schema')!r}）")
    name = doc.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise _fail(where, "name", f"{name!r} 不合格式", "小寫英數、底線、連字號，最長 40 字")
    out: dict[str, Any] = {"schema": SCHEMA, "name": name, **copy.deepcopy(BASE)}
    for key, limit in (("label", MAX_LABEL), ("description", MAX_DESCRIPTION)):
        value = doc.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise _fail(where, key, f"要是字串，最長 {limit} 個字")
        out[key] = value.strip()
    out["label"] = out["label"] or name
    out = {k: out[k] for k in ("schema", "name", "label", "description", *BASE)}

    def choice(value: Any, path: str, options: tuple[str, ...]) -> str:
        if value not in options:
            raise _fail(where, path, f"不認得 {value!r}", f"可用：{', '.join(options)}")
        return str(value)

    def color(value: Any, path: str) -> str:
        if not is_color(value):
            raise _fail(where, path, f"不認得的顏色 {value!r}",
                        f"可用：{', '.join(COLORS)}，或 #RRGGBB")
        return str(value)

    def boolean(value: Any, path: str) -> bool:
        if not isinstance(value, bool):
            raise _fail(where, path, "要是 true 或 false")
        return value

    def section(key: str) -> dict[str, Any]:
        value = doc.get(key, {})
        if not isinstance(value, dict):
            raise _fail(where, key, "要是物件")
        extra = sorted(set(value) - set(BASE[key]))
        if extra:
            raise _fail(where, key, f"不認得的欄位 {', '.join(extra)}",
                        f"可用欄位：{', '.join(BASE[key])}")
        return value

    if "aspect" in doc:
        out["aspect"] = choice(doc["aspect"], "aspect", tuple(ASPECTS))
    if "fit" in doc:
        out["fit"] = choice(doc["fit"], "fit", FITS)

    s = section("subtitles")
    sub = out["subtitles"]
    if "size" in s:
        sub["size"] = choice(s["size"], "subtitles.size", SUBTITLE_SIZES)
    for key in ("color", "en_color"):
        if key in s:
            sub[key] = color(s[key], f"subtitles.{key}")
    if s.get("margin") is not None:
        m = s["margin"]
        if isinstance(m, bool) or not isinstance(m, (int, float)) or not 0 <= m <= MAX_MARGIN:
            raise _fail(where, "subtitles.margin", f"要是 0–{MAX_MARGIN:g} 的數字或 null",
                        "字幕離底邊佔畫面高度的比例；0.2＝離底 20%")
        sub["margin"] = float(m)
    if "box" in s:
        sub["box"] = boolean(s["box"], "subtitles.box")
    if "font" in s:
        font = s["font"]
        if not isinstance(font, str) or any(c in font for c in ",\n\r"):
            raise _fail(where, "subtitles.font", "要是字串，不能含逗號或換行")
        sub["font"] = font.strip()

    for key, sizes in (("title", TITLE_SIZES), ("series", OVERLAY_SIZES)):
        o = section(key)
        dest = out[key]
        if "show" in o:
            dest["show"] = boolean(o["show"], f"{key}.show")
        if "position" in o:
            dest["position"] = choice(o["position"], f"{key}.position", POSITIONS)
        if "size" in o:
            dest["size"] = choice(o["size"], f"{key}.size", sizes)
        if "color" in o:
            dest["color"] = color(o["color"], f"{key}.color")

    mu = section("music")
    if "volume" in mu:
        v = mu["volume"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 2:
            raise _fail(where, "music.volume", "要是 0–2 的數字", "0.22 左右是不搶旁白的背景音量")
        out["music"]["volume"] = float(v)
    return out


# ------------------------------------------------------------------ 找、列、存、刪

def _builtin_root() -> Any:
    return resources.files("vidpipe").joinpath("data/templates")


def builtin() -> dict[str, dict[str, Any]]:
    out = {}
    first = f"{DEFAULT}.json"   # classic 排第一
    for entry in sorted(_builtin_root().iterdir(), key=lambda e: (e.name != first, e.name)):
        if entry.name.endswith(".json"):
            tpl = parse(json.loads(entry.read_text(encoding="utf-8")), where=f" {entry.name}")
            out[tpl["name"]] = tpl
    return out


def user_dir(cfg: Config) -> Path:
    return Path(cfg.template_dir).expanduser()


def _read_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput(f"模板 {path.name} 不是合法的 JSON（{exc}）") from exc
    return parse(data, where=f" {path.name}")


def list_all(cfg: Config) -> list[dict[str, Any]]:
    """內建的在前、自己存的在後。壞掉的檔案也列出來（帶 `error`），網頁才看得到哪裡錯。"""
    items = [{"name": n, "builtin": True, "template": t} for n, t in builtin().items()]
    taken = {i["name"] for i in items}
    root = user_dir(cfg)
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            item: dict[str, Any] = {"name": path.stem, "builtin": False, "path": str(path)}
            try:
                item["template"] = _read_file(path)
                if item["template"]["name"] != path.stem:
                    raise BadInput(f"檔名 {path.name} 跟 name {item['template']['name']!r} 不一樣")
                if path.stem in taken:
                    raise BadInput(f"{path.stem} 是內建模板的名字，這個檔案不會被用到",
                                   hint="改個名字")
            except BadInput as exc:
                item.pop("template", None)
                item["error"] = exc.message
            items.append(item)
    return items


def get(ref: str | None, cfg: Config | None = None) -> dict[str, Any]:
    """名稱或 .json 檔的路徑 → 模板。空的 = 設定的 default_template（再沒有就 classic）。"""
    cfg = cfg or Config()
    ref = (ref or cfg.default_template or DEFAULT).strip()
    if ref.endswith(".json") or os.sep in ref or "/" in ref:
        path = Path(ref).expanduser()
        if not path.is_file():
            raise NotFound(f"找不到模板檔：{ref}")
        return _read_file(path)
    if not NAME_RE.match(ref):
        raise BadInput(f"模板名稱 {ref!r} 不合格式", hint="小寫英數、底線、連字號")
    found = builtin()
    if ref in found:
        return found[ref]
    path = user_dir(cfg) / f"{ref}.json"
    if path.is_file():
        return _read_file(path)
    names = [i["name"] for i in list_all(cfg) if "template" in i]
    raise NotFound(f"沒有叫 {ref} 的模板", hint=f"可用：{', '.join(names)}")


def save(doc: Any, cfg: Config, *, name: str | None = None) -> dict[str, Any]:
    """存成 `template_dir/<name>.json`（同名就覆蓋）。內建模板的名字不能用。"""
    if isinstance(doc, dict) and name:
        doc = {**doc, "name": name}
    if isinstance(doc, dict) and "schema" not in doc:
        doc = {"schema": SCHEMA, **doc}
    tpl = parse(doc)
    if tpl["name"] in builtin():
        raise BadInput(f"{tpl['name']} 是內建模板，不能覆蓋", hint="另取一個名字存成自己的模板")
    root = user_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{tpl['name']}.json"
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(compact(tpl), ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"name": tpl["name"], "path": str(path), "template": tpl}


def delete(name: str, cfg: Config) -> dict[str, Any]:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise BadInput(f"模板名稱 {name!r} 不合格式")
    if name in builtin():
        raise BadInput(f"{name} 是內建模板，不能刪除")
    path = user_dir(cfg) / f"{name}.json"
    if not path.is_file():
        raise NotFound(f"沒有叫 {name} 的自訂模板")
    path.unlink()
    return {"name": name, "deleted": True}


def compact(tpl: dict[str, Any]) -> dict[str, Any]:
    """存檔用：只留跟 classic 不一樣的欄位，檔案短、一眼看得出改了什麼。"""
    out: dict[str, Any] = {"schema": SCHEMA, "name": tpl["name"]}
    for key in ("label", "description"):
        if tpl.get(key) and tpl[key] != (tpl["name"] if key == "label" else ""):
            out[key] = tpl[key]
    for key, base in BASE.items():
        if isinstance(base, dict):
            diff = {k: v for k, v in tpl[key].items() if v != base[k]}
            if diff:
                out[key] = diff
        elif tpl[key] != base:
            out[key] = tpl[key]
    return out


# ------------------------------------------------------------------ 套用

def manifest_parts(tpl: dict[str, Any], *, title: str, series: str = "",
                   aspect: str | None = None, subtitle_size: str | None = None
                   ) -> dict[str, Any]:
    """模板 → manifest 的片段。`aspect`、`subtitle_size` 有給就蓋過模板（文案自己指定的優先）。

    回 `{"aspect", "fit", "subtitles", "overlays", "music_volume"}`；`subtitles` 只放跟
    manifest 預設不同的樣式欄位，classic 組出來的 manifest 跟原本一樣。
    """
    aspect = aspect or tpl["aspect"]
    portrait = aspect != "16:9"
    sub = tpl["subtitles"]
    subtitles: dict[str, Any] = {"burn_in": True, "size": subtitle_size or sub["size"]}
    defaults = {"color": "white", "en_color": "paleyellow", "margin": None, "box": False,
                "font": ""}
    subtitles.update({k: sub[k] for k, v in defaults.items() if sub[k] != v})
    overlays: list[dict[str, str]] = []
    t = tpl["title"]
    if t["show"] and title:
        size = t["size"] if t["size"] != "auto" else ("xlarge" if portrait else "medium")
        overlays.append({"text": title, "position": t["position"], "color": t["color"],
                         "size": size})
    s = tpl["series"]
    if s["show"] and series:
        overlay = {"text": series, "position": s["position"], "size": s["size"]}
        # white 是 manifest 的預設；不寫，classic 組出的 manifest 才跟以前一字不差
        if s["color"] != "white":
            overlay["color"] = s["color"]
        overlays.append(overlay)
    return {"aspect": aspect, "fit": tpl["fit"], "subtitles": subtitles, "overlays": overlays,
            "music_volume": tpl["music"]["volume"]}


def summary(tpl: dict[str, Any]) -> dict[str, Any]:
    return {"name": tpl["name"], "label": tpl["label"], "description": tpl["description"]}
