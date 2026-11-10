"""文案直出：一段文案 → 分鏡 → 挑畫面 → manifest → 成片。不需要 LLM。

    {"id": "letting-go-01", "title": "為什麼越想放下，反而越放不下？",   // 都是選填
     "text": "為什麼越想放下，反而越放不下？\\n\\n睡前一躺下，白天的事又自己跑出來。…"}

做法（都是規則，同樣的輸入得到同樣的分鏡與主題）：

1. **分鏡**：空白行隔開的每一段是一個場景；段落再依句子切成「鏡頭」（每段不超過 26 個字），
   一個鏡頭一段畫面，所以大約 4 到 6 秒換一次畫面。段落裡 `畫面：ocean` 那行可以直接指定主題。
2. **畫面主題**：旁白裡出現主題的關鍵詞就選那個主題（`data/footage.json`）；都沒對到就輪流用
   幾個安靜的空鏡主題。相鄰場景不用同一個主題。
3. **素材**：每個主題向 vidgrab 搜一批（YouTube，優先授權明確的）。**別的作品用過的素材不再用**
   （`work_dir/_quick/used.json`）；沒用過的不夠時換一組關鍵字再搜，真的沒有才重複使用並警告。
4. **版面**：上方常駐標題（預設用第一句）、下方系列名稱、加大的字幕、背景音樂。

`build` 只組 manifest（不呼叫 TTS、不下載）；`run` = `build` ＋ `shorts.make`。
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from . import assets, jobs, timeline
from . import manifest as manifest_mod
from .clients.vidgrab import VidgrabClient
from .config import Config
from .errors import BadInput, UpstreamError, VidpipeError
from .manifest import ID_RE, SUBTITLE_SIZES

REQUEST_FILE = "quick.json"
REPORT_FILE = "quick-report.json"
BEAT_CHARS = 26                 # 一個鏡頭最多幾個字
TITLE_CHARS = 20
MAX_TEXT_CHARS = 1200           # 約 4–5 分鐘；Shorts 用不到這麼長
MAX_BATCH = 50
MAX_NEW_SEARCHES = 3            # 一個主題一次最多再搜幾組新的關鍵字
MIN_CLIP_SECONDS = 40
LICENSE_RANK = {"free": 0, "free-attribution": 1, "check": 2, "unknown": 3}
OPTION_KEYS = {"series", "music", "allow_unknown_license", "subtitle_size", "aspect"}
REQUEST_KEYS = {"id", "title", "text", *OPTION_KEYS}

_PUNCT = re.compile(r"[\s，。！？；：、「」『』（）《》〈〉…—\-,.!?;:'\"()]+")
_SENTENCE = re.compile(r"[^。！？!?；;…]+[。！？!?；;…」』）]*")
_THEME_LINE = re.compile(r"^\s*(?:畫面|visual)\s*[:：]\s*(\S+)\s*$", re.I)
_FULLWIDTH = str.maketrans({",": "，", "?": "？", "!": "！", ":": "：", ";": "；", "(": "（",
                             ")": "）"})
_CJK = r"一-鿿"


# ------------------------------------------------------------------ 文字

def chars(text: str) -> int:
    """不含標點與空白的字數。"""
    return len(_PUNCT.sub("", text))


def normalize(text: str) -> str:
    """中文旁白的標點一律全形、去掉空白（字幕直接顯示這些字）。"""
    text = text.translate(_FULLWIDTH)
    text = re.sub(rf"(?<=[{_CJK}])\.(?=\s|$|[{_CJK}])", "。", text)
    return re.sub(r"\s+", "", text) if re.search(rf"[{_CJK}]", text) else text.strip()


def split_beats(text: str, max_chars: int = BEAT_CHARS) -> list[str]:
    """一個場景的旁白 → 幾個鏡頭：在句子邊界切，每段盡量不超過 `max_chars` 個字。"""
    parts = [p for p in _SENTENCE.findall(text) if chars(p)]
    if not parts:
        return [text]
    beats: list[str] = []
    cur = ""
    for part in parts:
        if cur and chars(cur) + chars(part) > max_chars:
            beats.append(cur)
            cur = part
        else:
            cur += part
    if cur:
        if beats and chars(cur) < 7:      # 太短的尾巴併回前一段，免得畫面閃一下
            beats[-1] += cur
        else:
            beats.append(cur)
    return beats


def default_title(text: str) -> str:
    """沒給標題：用第一句；太長就取到第一個逗號。"""
    first = (_SENTENCE.findall(text) or [text])[0].strip()
    if chars(first) > TITLE_CHARS:
        first = re.split(r"[，,、]", first)[0]
    return first[:TITLE_CHARS + 4]


def make_id(text: str, today: dt.date | None = None) -> str:
    day = (today or dt.date.today()).strftime("%Y%m%d")
    return f"q-{day}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"


# ------------------------------------------------------------------ 請求

def _fail(path: str, message: str, hint: str = "") -> BadInput:
    return BadInput(f"文案 {path}：{message}", hint=hint)


def parse_request(doc: Any, *, today: dt.date | None = None) -> dict[str, Any]:
    """驗證一筆文案，補上預設值。回的 dict 可以直接存檔、交給 `run`。"""
    if not isinstance(doc, dict):
        raise _fail("（最上層）", "要是物件", '例：{"text": "文案…", "title": "標題"}')
    unknown = sorted(set(doc) - REQUEST_KEYS)
    if unknown:
        raise _fail("（最上層）", f"不認得的欄位 {', '.join(unknown)}",
                    f"可用欄位：{', '.join(sorted(REQUEST_KEYS))}")
    text = doc.get("text")
    if not isinstance(text, str) or not chars(text):
        raise _fail("text", "要有文案", "空白行隔開場景；每個場景一到三句")
    if chars(text) > MAX_TEXT_CHARS:
        raise _fail("text", f"太長了（{chars(text)} 字，上限 {MAX_TEXT_CHARS}）",
                    "Shorts 大約 130–540 字；長文請拆成幾支")
    out: dict[str, Any] = {"text": text.replace("\r\n", "\n").strip()}
    short_id = doc.get("id") or make_id(out["text"], today)
    if not isinstance(short_id, str) or not ID_RE.match(short_id):
        raise _fail("id", f"{short_id!r} 不合格式", "小寫英數、底線、連字號，最長 64 字")
    out["id"] = short_id
    title = doc.get("title")
    if title is not None and not isinstance(title, str):
        raise _fail("title", "要是字串")
    out["title"] = normalize(title) if title and title.strip() else \
        normalize(default_title(_narration_only(out["text"])))
    if len(out["title"]) > manifest_mod.MAX_OVERLAY_CHARS:
        raise _fail("title", f"最長 {manifest_mod.MAX_OVERLAY_CHARS} 個字")
    series = doc.get("series", "")
    if not isinstance(series, str) or len(series) > manifest_mod.MAX_OVERLAY_CHARS:
        raise _fail("series", f"要是字串，最長 {manifest_mod.MAX_OVERLAY_CHARS} 個字")
    out["series"] = series.strip()
    for key, default in (("music", True), ("allow_unknown_license", True)):
        value = doc.get(key, default)
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", ""):
            value = value.strip().lower() in ("true", "1") if value.strip() else default
        if not isinstance(value, bool):
            raise _fail(key, "要是 true 或 false")
        out[key] = value
    size = doc.get("subtitle_size") or "large"
    if size not in SUBTITLE_SIZES:
        raise _fail("subtitle_size", f"不認得 {size!r}", f"可用：{', '.join(SUBTITLE_SIZES)}")
    out["subtitle_size"] = size
    aspect = doc.get("aspect") or "9:16"
    if aspect not in ("9:16", "16:9", "1:1"):
        raise _fail("aspect", f"不認得 {aspect!r}", "可用：9:16、16:9、1:1")
    out["aspect"] = aspect
    if not scenes_of(out["text"], library()):
        raise _fail("text", "沒有可以唸的字")
    return out


def _narration_only(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _THEME_LINE.match(line))


def parse_batch(data: Any, *, fmt: str = "", shared: dict[str, Any] | None = None,
                today: dt.date | None = None) -> list[dict[str, Any]]:
    """批次：JSON（清單，或 `{"items": [...]}`）、JSONL、CSV（要有 `text` 欄）。

    `data` 是已經解析的物件或原始文字；`fmt` 是 `json`／`jsonl`／`csv`，空的就自己判斷。
    每一筆的欄位跟單筆一樣；`shared` 是套用到每一筆的選項（各筆自己寫的優先）；id 重複會報錯。
    """
    items: Any = data
    if isinstance(data, str):
        raw = data.lstrip("﻿").strip()
        kind = fmt.lower() or ("json" if raw[:1] in "[{" else "csv")
        try:
            if kind == "json":
                items = json.loads(raw)
            elif kind == "jsonl":
                items = [json.loads(line) for line in raw.splitlines() if line.strip()]
            elif kind == "csv":
                rows = list(csv.DictReader(io.StringIO(raw)))
                if rows and "text" not in rows[0]:
                    raise BadInput("CSV 要有 text 欄（第一列是欄位名稱）",
                                   hint="欄位：text（必填）、id、title、series、music…")
                items = [{k: v for k, v in row.items() if k and v not in (None, "")}
                         for row in rows]
            else:
                raise BadInput(f"不認得的批次格式：{fmt}", hint="可用：json、jsonl、csv")
        except ValueError as exc:
            raise BadInput(f"批次內容不是合法的 {kind.upper()}：{exc}") from exc
    if isinstance(items, dict):
        items = items.get("items")
    if not isinstance(items, list) or not items:
        raise BadInput("批次要是非空的清單", hint='JSON：[{"text": "…"}, …]；CSV：第一列是欄位名稱')
    if len(items) > MAX_BATCH:
        raise BadInput(f"一批最多 {MAX_BATCH} 筆（收到 {len(items)}）")
    out, seen = [], set()
    for i, item in enumerate(items):
        try:
            if shared and isinstance(item, dict):
                item = {**shared, **item}
            req = parse_request(item, today=today)
        except BadInput as exc:
            raise BadInput(f"第 {i + 1} 筆：{exc.message}", hint=exc.hint) from None
        if req["id"] in seen:
            raise BadInput(f"第 {i + 1} 筆：id {req['id']!r} 重複了",
                           hint="兩筆文案一模一樣，或 id 寫了一樣的")
        seen.add(req["id"])
        out.append(req)
    return out


# ------------------------------------------------------------------ 主題

def library() -> dict[str, Any]:
    text = resources.files("vidpipe").joinpath("data/footage.json").read_text(encoding="utf-8")
    return json.loads(text)


def scenes_of(text: str, lib: dict[str, Any]) -> list[dict[str, Any]]:
    """文案 → 場景（段落）。每個場景：`text`、`theme`（指定的，沒指定是 None）。"""
    scenes = []
    for para in re.split(r"\n\s*\n", text.strip()):
        theme = None
        lines = []
        for line in para.splitlines():
            m = _THEME_LINE.match(line)
            if m:
                if m.group(1) not in lib["themes"]:
                    raise _fail("text", f"不認得的畫面主題 {m.group(1)!r}",
                                f"可用：{', '.join(lib['themes'])}")
                theme = m.group(1)
            elif line.strip():
                lines.append(line.strip())
        body = normalize("".join(lines))
        if chars(body):
            scenes.append({"text": body, "theme": theme})
    return scenes


def pick_theme(text: str, lib: dict[str, Any], *, previous: str | None, index: int) -> str:
    """旁白裡出現哪個主題的關鍵詞最多就選它；沒對到就輪流用安靜的空鏡。不跟上一個場景一樣。"""
    scored = []
    for name, spec in lib["themes"].items():
        hits = sum(len(word) for word in spec.get("keywords", []) if word in text)
        if hits and name != previous:
            scored.append((-hits, list(lib["themes"]).index(name), name))
    if scored:
        return min(scored)[2]
    defaults = [d for d in lib.get("defaults") or list(lib["themes"]) if d != previous]
    return defaults[index % len(defaults)]


def storyboard(text: str, lib: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """文案 → 鏡頭清單：`id`（s<場景>-<鏡頭>）、`text`、`theme`。"""
    lib = lib or library()
    beats, previous = [], None
    for i, scene in enumerate(scenes_of(text, lib), 1):
        theme = scene["theme"] or pick_theme(scene["text"], lib, previous=previous, index=i - 1)
        previous = theme
        for j, part in enumerate(split_beats(scene["text"]), 1):
            beats.append({"id": f"s{i}-{j}", "text": part, "theme": theme})
    return beats


# ------------------------------------------------------------------ 素材

def _state_dir(cfg: Config) -> Path:
    return Path(cfg.work_dir) / "_quick"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timeline._atomic_json(path, data)


def used_assets(cfg: Config) -> dict[str, list[str]]:
    """素材 id → 用過它的作品 id。"""
    data = _read(_state_dir(cfg) / "used.json", {})
    return data if isinstance(data, dict) else {}


def record_use(cfg: Config, short_id: str, asset_ids: list[str]) -> None:
    """記下這支片用了哪些素材（重做同一支片會先清掉它舊的紀錄）。"""
    used = {k: [s for s in v if s != short_id] for k, v in used_assets(cfg).items()}
    for asset_id in asset_ids:
        used.setdefault(asset_id, []).append(short_id)
    _write(_state_dir(cfg) / "used.json", {k: v for k, v in used.items() if v})


def _unit(*parts: str) -> float:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _rank(item: dict[str, Any]) -> int:
    return LICENSE_RANK.get((item.get("license") or {}).get("kind") or "unknown", 3)


def usable(item: dict[str, Any], lib: dict[str, Any], allow_unknown: bool) -> bool:
    title = (item.get("title") or "").lower()
    if any(word.lower() in title for word in lib.get("exclude_title", [])):
        return False
    if (item.get("duration") or 0) < MIN_CLIP_SECONDS:
        return False
    return _rank(item) <= 1 or allow_unknown


def _search(client: VidgrabClient, project: str, query: str, options: dict[str, Any]
            ) -> list[dict[str, Any]]:
    """向 vidgrab 搜一組關鍵字（存成它的專案 `project`），回候選。搜不到東西回空清單。"""
    body = {"keywords": [query], "name": project, **options}
    job = client.request("POST", "/api/search", json=body, timeout=60).json().get("job")
    if not job:
        raise UpstreamError("vidgrab 沒有回搜尋工作的 id", upstream="vidgrab")
    try:
        result = client._wait_job(str(job), 600.0, 2.0)
    except UpstreamError as exc:
        if "沒搜到" in exc.message:
            return []
        raise
    name = str(result.get("project") or project)      # vidgrab 回報實際用的專案名
    found = client.get_json("/api/vault?project=" + _quote(name), timeout=60)
    return list(found.get("items") or [])


def _quote(text: str) -> str:
    import urllib.parse
    return urllib.parse.quote(text, safe="")


_KEEP = ("id", "title", "author", "duration", "license", "width", "height", "webpage_url",
         "credit_hint")


def theme_pool(cfg: Config, client: VidgrabClient, lib: dict[str, Any], theme: str, *,
               need: int, banned: set[str], allow_unknown: bool,
               warnings: list[str]) -> list[dict[str, Any]]:
    """這個主題可用的素材（授權好的在前）。沒用過的不到 `need` 支就換關鍵字再搜。

    狀態存在 `work_dir/_quick/pools/<theme>.json`：搜過哪幾組關鍵字、找到哪些素材。
    """
    path = _state_dir(cfg) / "pools" / f"{theme}.json"
    state = _read(path, {"searched": [], "items": []})
    base = lib["themes"][theme]["queries"]
    stem = base[0].split(" free stock")[0]
    variants = list(base) + [f"{stem} {m} free stock footage no copyright"
                             for m in lib.get("modifiers", [])]

    def fresh() -> list[dict[str, Any]]:
        return [it for it in state["items"]
                if it["id"] not in banned and usable(it, lib, allow_unknown)]

    new_searches = 0
    for n, query in enumerate(variants):
        if len(fresh()) >= need or new_searches >= MAX_NEW_SEARCHES:
            break
        if query in state["searched"]:
            continue
        jobs.stage("search", f"{theme}：{query}")
        found = _search(client, f"vq-{theme}-{n}", query, lib["search"])
        new_searches += 1
        state["searched"].append(query)
        known = {it["id"] for it in state["items"]}
        state["items"] += [{k: it.get(k) for k in _KEEP} for it in found if it["id"] not in known]
        _write(path, state)
    pool = fresh()
    if len(pool) < need:      # 真的沒有新的了：把用過的也放進來（排在後面）
        reuse = [it for it in state["items"]
                 if it["id"] in banned and usable(it, lib, allow_unknown)]
        if reuse and not pool:
            warnings.append(f"主題 {theme} 沒有沒用過的素材了，重複使用其他作品用過的")
        pool += reuse
    pool.sort(key=lambda it: (it["id"] in banned, _rank(it), -(it.get("height") or 0)))
    return pool


def assign(beats: list[dict[str, Any]], pools: dict[str, list[dict[str, Any]]],
           seed: str, cps: float = 4.2) -> list[dict[str, Any]]:
    """每個鏡頭 → `{scene, asset_id, start, item}`。同一支片先用沒用過的；相鄰鏡頭不用同一支
    （除非只剩它）；同一支素材用第二次時，起點挑離之前用過的位置最遠的。"""
    used: dict[str, int] = {}
    starts: dict[str, list[float]] = {}
    out: list[dict[str, Any]] = []
    for beat in beats:
        cands = pools.get(beat["theme"]) or []
        if not cands:
            raise BadInput(f"鏡頭 {beat['id']} 的主題 {beat['theme']} 找不到可用的素材",
                           hint="換一個主題（段落裡加一行「畫面：sky」），或放行授權未標示的素材")
        best = min(_rank(it) for it in cands)
        tier = [it for it in cands if _rank(it) == best] or cands
        last = out[-1]["asset_id"] if out else None
        tier.sort(key=lambda it: (it["id"] == last, used.get(it["id"], 0),
                                  _unit(seed, beat["id"], it["id"])))
        item = tier[0]
        used[item["id"]] = used.get(item["id"], 0) + 1
        seconds = chars(beat["text"]) / cps + 2.0
        duration = float(item.get("duration") or 0)
        room = max(0.0, duration * 0.58 - seconds)
        before = starts.setdefault(item["id"], [])
        options = [duration * 0.12 + room * ((_unit(seed, item["id"]) + k / 12) % 1.0)
                   for k in range(12)]
        start = round(max(options, key=lambda x: min((abs(x - y) for y in before), default=0.0))
                      if before else options[0], 1)
        before.append(start)
        out.append({"scene": beat["id"], "asset_id": item["id"], "start": start, "item": item})
    return out


def pick_footage(cfg: Config, short_id: str, beats: list[dict[str, Any]], *,
                 allow_unknown: bool, client: VidgrabClient | None = None,
                 lib: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """替一支片的每個鏡頭挑素材，回 (picks, warnings)。別的作品用過的素材不會再用。"""
    lib = lib or library()
    client = client or assets.make_client(cfg)
    warnings: list[str] = []
    banned = {a for a, shorts in used_assets(cfg).items() if any(s != short_id for s in shorts)}
    pools: dict[str, list[dict[str, Any]]] = {}
    for theme in sorted({b["theme"] for b in beats}):
        need = min(4, sum(1 for b in beats if b["theme"] == theme))
        pools[theme] = theme_pool(cfg, client, lib, theme, need=need, banned=banned,
                                  allow_unknown=allow_unknown, warnings=warnings)
        if not pools[theme]:       # 這個主題沒有素材：借用安靜的空鏡，不讓整支片失敗
            for other in lib.get("defaults", []):
                pools[theme] = theme_pool(cfg, client, lib, other, need=need, banned=banned,
                                          allow_unknown=allow_unknown, warnings=warnings)
                if pools[theme]:
                    warnings.append(f"主題 {theme} 沒有素材，改用 {other}")
                    break
    return assign(beats, pools, short_id), warnings


def pick_music(cfg: Config, short_id: str, client: VidgrabClient, lib: dict[str, Any],
               warnings: list[str]) -> str | None:
    """背景音樂：vidgrab 音樂庫（CC0／CC BY），每支片固定挑一首。找不到就不放。"""
    path = _state_dir(cfg) / "music.json"
    tracks = _read(path, None)
    if tracks is None:
        jobs.stage("music", "找背景音樂")
        try:
            found = client.search(lib.get("music_queries", ["calm piano"]), platform="music",
                                  orientation="any", per_keyword=10)["candidates"]
        except VidpipeError as exc:
            warnings.append(f"找不到背景音樂（{exc.message}），這支片沒有配樂")
            return None
        tracks = [{k: it.get(k) for k in _KEEP} for it in found
                  if _rank(it) <= 1 and (it.get("duration") or 0) >= 120]
        _write(path, tracks)
    if not tracks:
        warnings.append("音樂庫沒有可用的曲目，這支片沒有配樂")
        return None
    return tracks[int(_unit(short_id, "music") * len(tracks)) % len(tracks)]["id"]


# ------------------------------------------------------------------ 組 manifest、做成片

def build(req: dict[str, Any], cfg: Config, *, client: VidgrabClient | None = None
          ) -> dict[str, Any]:
    """文案 → `{"manifest": 可以直接 make 的 manifest, "report": 分鏡與挑選報告}`。

    只搜尋，不呼叫 TTS、不下載。
    """
    lib = library()
    client = client or assets.make_client(cfg)
    beats = storyboard(req["text"], lib)
    picks, warnings = pick_footage(cfg, req["id"], beats, client=client, lib=lib,
                                   allow_unknown=req["allow_unknown_license"])
    portrait = req["aspect"] != "16:9"
    overlays = [{"text": req["title"], "position": "top", "color": "yellow",
                 "size": "xlarge" if portrait else "medium"}]
    if req["series"]:
        overlays.append({"text": req["series"], "position": "bottom", "size": "small"})
    doc: dict[str, Any] = {
        "schema": manifest_mod.SCHEMA, "id": req["id"], "title": req["title"],
        "output": {"aspect": req["aspect"], "fps": 30},
        "scenes": [{"id": b["id"], "text": b["text"], "fit": "crop",
                    "clip": {"asset_id": p["asset_id"], "start": p["start"]}}
                   for b, p in zip(beats, picks, strict=True)],
        "subtitles": {"burn_in": True, "size": req["subtitle_size"]},
        "overlays": overlays, "cover": {"text": req["title"]}}
    if req["music"]:
        track = pick_music(cfg, req["id"], client, lib, warnings)
        if track:
            doc["music"] = {"asset_id": track, "start": 4, "volume": 0.22}
    kinds = [(p["item"].get("license") or {}).get("kind") or "unknown" for p in picks]
    report = {"id": req["id"], "title": req["title"], "chars": chars(req["text"]),
              "estimated_seconds": round(chars(req["text"]) / 4.2 + len(beats) * 0.4, 1),
              "beats": [{"id": b["id"], "theme": b["theme"], "text": b["text"],
                         "asset_id": p["asset_id"], "start": p["start"],
                         "title": p["item"].get("title"),
                         "license": (p["item"].get("license") or {}).get("kind")}
                        for b, p in zip(beats, picks, strict=True)],
              "licenses": {k: kinds.count(k) for k in sorted(set(kinds))},
              "warnings": warnings}
    manifest_mod.parse(doc)        # 自己組的 manifest 也照同一套規則驗一次
    return {"manifest": doc, "report": report}


def store_request(cfg: Config, req: dict[str, Any]) -> Path:
    root = timeline.short_dir(cfg, req["id"])
    root.mkdir(parents=True, exist_ok=True)
    timeline._atomic_json(root / REQUEST_FILE, req)
    return root / REQUEST_FILE


def run(req: dict[str, Any], cfg: Config, *, client: VidgrabClient | None = None,
        output: str | None = None) -> dict[str, Any]:
    """一筆文案做到成片：分鏡 → 挑素材 → 旁白 → 渲染 → 封面與 credits。"""
    from . import shorts

    jobs.stage("storyboard", "分鏡、挑畫面")
    built = build(req, cfg, client=client)
    doc = built["manifest"]
    store_request(cfg, req)
    _write(timeline.short_dir(cfg, req["id"]) / REPORT_FILE, built["report"])
    manifest, effective = shorts.parse_document(doc, cfg)
    result = shorts.make(manifest, doc, effective, output)
    record_use(cfg, req["id"], sorted({b["asset_id"] for b in built["report"]["beats"]}))
    result["warnings"] = [*built["report"]["warnings"], *result.get("warnings", [])]
    result["quick"] = {k: built["report"][k] for k in ("chars", "estimated_seconds", "licenses")}
    return result


def run_batch(reqs: list[dict[str, Any]], cfg: Config, *, client: VidgrabClient | None = None
              ) -> dict[str, Any]:
    """一批文案，一支接一支做。一支失敗不影響其他支；結果逐筆列出。"""
    results = []
    for i, req in enumerate(reqs, 1):
        jobs.stage("item", f"{i}/{len(reqs)}　{req['id']}")
        try:
            out = run(req, cfg, client=client)
            results.append({"id": req["id"], "ok": True, "path": out["path"],
                            "duration": out["duration"], "warnings": out["warnings"]})
        except VidpipeError as exc:
            if isinstance(exc, jobs.Cancelled):
                raise
            results.append({"id": req["id"], "ok": False, "error": exc.to_dict()})
    failed = [r["id"] for r in results if not r["ok"]]
    return {"total": len(results), "done": len(results) - len(failed), "failed": failed,
            "items": results}
