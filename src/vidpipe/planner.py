"""plan：把「腳本草稿」補成可以直接 `make` 的 manifest——替沒指定素材的場景去 vidgrab 找一支，
需要的話也挑一首背景音樂。

草稿就是 manifest，只是：

- 場景可以沒有 `clip`，改給 `query`（搜尋關鍵字，字串或清單；**英文效果最好**，素材站的索引
  對中文很薄）。沒給 `query` 就拿旁白文字去搜，並在 `warnings` 提醒。給了 `clip` 的場景不動
  （`clip` 與 `query` 同時給會報錯）。
- `music` 可以給 `query` 代替 `path`／`asset_id`，其他欄位（`volume`…）照舊。
- 選填的最上層 `search`：`platform`（預設 `stock`＝Pexels＋Pixabay）、`orientation`
  （預設 `portrait`）、`per_keyword`（8）、`min_height`（720）、`allow_unknown_license`
  （false）、`music_platform`（`music`）、`timeout`（每次搜尋最多等幾秒，300）。
- 場景 `id` 與 `schema` 可以省（`s1`、`s2`…）；`id`（作品）一定要有。

選片規則（每個場景獨立挑，**可解釋**，不是黑箱）：依序比較

1. **授權**：只考慮 `free`（可自由使用）與 `free-attribution`（可用需標註），前者優先；
   `check`／`unknown` 預設不選（`allow_unknown_license: true` 才放行，而且會留警告）。
2. **長度夠不夠**：素材比場景預估長度（旁白字數粗估或 `min_duration`）加 0.5 秒還短的往後排——
   選了會循環補長。
3. **方向**：想要的方向（預設直式）優先，其次方形、尺寸不明，橫式最後（`fit` 會處理，
   只是裁掉較多）。
4. **解析度**：短邊低於 `min_height` 的往後排。
5. 以上都一樣就照 vidgrab 回的相關性順序。

同一支素材不會用在兩個場景，除非候選只剩它。結果的 `report` 把每個場景挑了誰、為什麼，以及
前幾名備選都列出來，agent 可以照需要換掉（改 manifest 的 `asset_id` 就好）。

不呼叫 TTS、不下載任何素材（只搜尋）；下載發生在 `render`。
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import assets, jobs, timeline
from . import manifest as manifest_mod
from .config import Config
from .errors import BadInput
from .manifest import Manifest

SCHEMA = "vidpipe.plan/1"
OK_LICENSES = ("free", "free-attribution")
LICENSE_RANK = {"free": 0, "free-attribution": 1, "check": 2, "unknown": 3}
MARGIN = 0.5                # 素材要比場景長這麼多才算「夠長」
ALTERNATIVES = 3            # 報告裡每個場景列幾個備選
PLATFORMS = ("stock", "pexels", "pixabay", "both", "bilibili", "youtube")
ORIENTATIONS = ("portrait", "landscape", "square", "any")
MUSIC_PLATFORMS = ("music", "openverse", "jamendo")
SEARCH_KEYS = {"platform", "orientation", "per_keyword", "min_height", "allow_unknown_license",
               "music_platform", "timeout"}
PLAN_FILE = "plan.json"
REQUEST_FILE = "plan-request.json"


class PlanError(BadInput):
    """草稿本身有問題（欄位寫錯）；訊息帶路徑。"""


def _fail(path: str, message: str, hint: str = "") -> PlanError:
    return PlanError(f"plan {path}：{message}", hint=hint)


# ------------------------------------------------------------------ 草稿

@dataclass(frozen=True)
class Settings:
    platform: str = "stock"
    orientation: str = "portrait"
    per_keyword: int = 8
    min_height: int = 720
    allow_unknown_license: bool = False
    music_platform: str = "music"
    timeout: float = 300.0


@dataclass(frozen=True)
class Request:
    draft: dict[str, Any]            # 去掉 plan 專用欄位的 manifest 原稿
    manifest: Manifest               # draft 解析的結果（用來估各場景長度）
    queries: dict[int, list[str]]    # 場景 index → 搜尋關鍵字（只有沒指定 clip 的場景）
    music_query: list[str] = field(default_factory=list)
    music_extra: dict[str, Any] = field(default_factory=dict)
    settings: Settings = field(default_factory=Settings)


def _keywords(value: Any, path: str) -> list[str]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(
            isinstance(x, str) and x.strip() for x in items):
        raise _fail(path, "要是非空字串，或非空字串的清單", '例：["night market", "street food"]')
    return [x.strip() for x in items]


def _settings(raw: Any) -> Settings:
    if raw is None:
        return Settings()
    if not isinstance(raw, dict):
        raise _fail("search", "要是物件")
    unknown = sorted(set(raw) - SEARCH_KEYS)
    if unknown:
        raise _fail("search", f"不認得的欄位 {', '.join(unknown)}",
                    f"可用欄位：{', '.join(sorted(SEARCH_KEYS))}")
    base = Settings()
    values: dict[str, Any] = {}
    for key, value in raw.items():
        default = getattr(base, key)
        if key in ("platform", "orientation", "music_platform"):
            options = {"platform": PLATFORMS, "orientation": ORIENTATIONS,
                       "music_platform": MUSIC_PLATFORMS}[key]
            if value not in options:
                raise _fail(f"search.{key}", f"不認得 {value!r}", f"可用：{', '.join(options)}")
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise _fail(f"search.{key}", "要是 true 或 false")
        else:
            lo, hi = {"per_keyword": (1, 30), "min_height": (0, 4320), "timeout": (5, 1800)}[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not lo <= value <= hi:
                raise _fail(f"search.{key}", f"要是 {lo}–{hi} 之間的數字")
            value = type(default)(value)
        values[key] = value
    return Settings(**values)


def parse_request(doc: Any, cfg: Config) -> Request:
    """草稿 → `Request`。manifest 的欄位照 manifest 的規則檢查（錯誤帶路徑），plan 專用的
    `query`、`search` 另外檢查。"""
    if not isinstance(doc, dict):
        raise _fail("（最上層）", "要是物件")
    draft = copy.deepcopy(doc)
    settings = _settings(draft.pop("search", None))
    draft.setdefault("schema", manifest_mod.SCHEMA)
    scenes = draft.get("scenes")
    queries: dict[int, list[str]] = {}
    if isinstance(scenes, list):
        for i, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue            # 交給 manifest 的檢查去報
            scene.setdefault("id", f"s{i + 1}")
            if "query" in scene:
                query = _keywords(scene.pop("query"), f"scenes[{i}].query")
                if "clip" in scene:
                    raise _fail(f"scenes[{i}]", "clip 與 query 只能給一個",
                                "已經指定素材就不用搜；要讓 plan 挑就拿掉 clip")
                queries[i] = query
    music_query: list[str] = []
    music_extra: dict[str, Any] = {}
    music = draft.get("music")
    if isinstance(music, dict) and "query" in music:
        if "path" in music or "asset_id" in music:
            raise _fail("music", "query 與 path／asset_id 只能給一個")
        music_query = _keywords(music.pop("query"), "music.query")
        music_extra = draft.pop("music")
    manifest = manifest_mod.parse(draft, subtitle_langs=cfg.subtitle_langs,
                                  burn_in=cfg.burn_subtitles)
    if music_extra:     # 先用一個假的 asset_id 驗證 music 其餘欄位（volume 範圍等）
        manifest_mod.parse({**draft, "music": {**music_extra, "asset_id": "plan:placeholder"}},
                           subtitle_langs=cfg.subtitle_langs, burn_in=cfg.burn_subtitles)
    return Request(draft, manifest, queries, music_query, music_extra, settings)


_KEYWORD_LINE = re.compile(r"^\s*(?:#|關鍵字[:：]|keywords?\s*:)\s*(.+?)\s*$", re.IGNORECASE)


def parse_script(text: str, *, short_id: str, title: str = "") -> dict[str, Any]:
    """純文字腳本 → 草稿：空白行隔開的每一段是一個場景；段落裡以 `#`、`關鍵字：`、
    `keywords:` 開頭的那行是這個場景的搜尋關鍵字（逗號分隔），其餘是旁白。"""
    scenes: list[dict[str, Any]] = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        if not para.strip():
            continue
        query: list[str] = []
        body: list[str] = []
        for line in para.strip().splitlines():
            match = _KEYWORD_LINE.match(line)
            if match:
                query = [k.strip() for k in re.split(r"[,，、]", match.group(1)) if k.strip()]
            else:
                body.append(line)
        scene: dict[str, Any] = {"id": f"s{len(scenes) + 1}"}
        narration = "\n".join(body).strip()
        if narration:
            scene["text"] = narration
        else:
            scene["min_duration"] = 2.0       # 只有關鍵字沒有旁白：留一小段畫面
        if query:
            scene["query"] = query
        scenes.append(scene)
    if not scenes:
        raise BadInput("腳本是空的", hint="空白行隔開每個場景；每段一句到幾句旁白")
    doc: dict[str, Any] = {"schema": manifest_mod.SCHEMA, "id": short_id, "scenes": scenes}
    if title:
        doc["title"] = title
    return doc


# ------------------------------------------------------------------ 選片

def orientation_of(candidate: dict[str, Any]) -> str:
    width, height = int(candidate.get("width") or 0), int(candidate.get("height") or 0)
    if not width or not height:
        return "unknown"
    if height > width * 1.05:
        return "portrait"
    if width > height * 1.05:
        return "landscape"
    return "square"


def _kind(candidate: dict[str, Any]) -> str:
    kind = (candidate.get("license") or {}).get("kind") or "unknown"
    return kind if kind in LICENSE_RANK else "unknown"


def _brief(c: dict[str, Any]) -> dict[str, Any]:
    lic = c.get("license") or {}
    return {"asset_id": c.get("id"), "title": c.get("title") or "", "author": c.get("author") or "",
            "license": {"kind": _kind(c), "label": lic.get("label") or ""},
            "duration": c.get("duration"), "width": c.get("width"), "height": c.get("height"),
            "orientation": orientation_of(c), "webpage_url": c.get("webpage_url") or "",
            "credit_hint": c.get("credit_hint") or ""}


def _short_side(c: dict[str, Any]) -> int:
    width, height = int(c.get("width") or 0), int(c.get("height") or 0)
    return min(width, height) if width and height else 0


def _why(c: dict[str, Any], need: float) -> str:
    parts = [f"授權{(c.get('license') or {}).get('label') or _kind(c)}"]
    o = orientation_of(c)
    parts.append({"portrait": "直式", "landscape": "橫式", "square": "方形"}.get(o, "尺寸不明"))
    dur = float(c.get("duration") or 0)
    if dur and need:
        parts.append(f"長度 {dur:.1f}s {'≥' if dur >= need + MARGIN - 0.05 else '<'} "
                     f"需要 {need:.1f}s")
    return "、".join(parts)


def choose(candidates: list[dict[str, Any]], need: float, settings: Settings, used: set[str],
           *, music: bool = False) -> tuple[dict[str, Any] | None, list[dict], list[str]]:
    """從候選挑一支。回 (選中的候選或 None, 備選的簡述, 說明這次選擇的注意事項)。"""
    wanted = settings.orientation if settings.orientation in ("portrait", "landscape", "square") \
        else "portrait"
    notes: list[str] = []
    eligible = [(i, c) for i, c in enumerate(candidates) if c.get("id")
                and (_kind(c) in OK_LICENSES or settings.allow_unknown_license)]
    skipped = sum(1 for c in candidates if _kind(c) not in OK_LICENSES) \
        if not settings.allow_unknown_license else 0
    if skipped:
        notes.append(f"{skipped} 支授權不明或要確認的候選沒有列入（allow_unknown_license 可放行）")
    fresh = [(i, c) for i, c in eligible if c["id"] not in used]
    if not fresh and eligible and not music:
        notes.append("可用的候選都已經用在別的場景，這個場景重複使用")
        fresh = eligible

    def key(item: tuple[int, dict[str, Any]]) -> tuple:
        i, c = item
        dur = float(c.get("duration") or 0)
        short = bool(dur) and dur < need + MARGIN - 0.05
        if music:
            return (LICENSE_RANK[_kind(c)], short, -dur if short else 0.0, i)
        o = orientation_of(c)
        orient = 0 if o == wanted else 1 if o in ("square", "unknown") else 2
        low = bool(_short_side(c)) and _short_side(c) < settings.min_height
        return (LICENSE_RANK[_kind(c)], short, orient, low, i)

    ranked = sorted(fresh, key=key)
    if not ranked:
        return None, [], notes
    best = ranked[0][1]
    if _kind(best) not in OK_LICENSES:
        label = (best.get("license") or {}).get("label") or "未知"
        notes.append(f"{best['id']} 的授權是「{label}」，公開發佈前要自己確認")
    dur = float(best.get("duration") or 0)
    if dur and need and dur < need + MARGIN - 0.05:
        notes.append(f"{best['id']} 只有 {dur:.1f} 秒，不夠 {need:.1f} 秒，渲染時會循環補長")
    if not music and orientation_of(best) not in (wanted, "unknown"):
        notes.append(f"{best['id']} 不是{'直式' if wanted == 'portrait' else wanted}，會多裁一些")
    alternatives = [dict(_brief(c), why=_why(c, need)) for _, c in ranked[1:1 + ALTERNATIVES]]
    return best, alternatives, notes


# ------------------------------------------------------------------ 執行

def _search_once(client: Any, keywords: list[str], platform: str, settings: Settings,
                 cache: dict[tuple, dict[str, Any]], log: list[dict[str, Any]]
                 ) -> list[dict[str, Any]]:
    key = (platform, tuple(k.lower() for k in keywords))
    if key not in cache:
        jobs.log(f"搜尋 {platform}：{'、'.join(keywords)}")
        found = client.search(keywords, platform=platform, orientation=settings.orientation,
                              per_keyword=settings.per_keyword, timeout=settings.timeout)
        cache[key] = found
        log.append({"keywords": keywords, "platform": platform, "project": found["project"],
                    "candidates": len(found["candidates"])})
    return cache[key]["candidates"]


def run(req: Request, cfg: Config, *, client: Any = None) -> dict[str, Any]:
    """搜尋、選片、組出完整 manifest。回 `{"schema", "manifest", "report"}`。"""
    from . import shorts  # 避免循環 import（shorts 在送件時才會用到 planner）

    client = client or assets.make_client(cfg)
    settings = req.settings
    described = shorts.describe(req.manifest)
    doc = copy.deepcopy(req.draft)
    scenes_out: list[dict[str, Any]] = []
    searches: list[dict[str, Any]] = []
    warnings: list[str] = []
    cache: dict[tuple, dict[str, Any]] = {}
    used: set[str] = set()
    total = len(req.manifest.scenes)
    jobs.stage("search", f"{total} 個場景")
    for i, scene in enumerate(req.manifest.scenes):
        need = described["scenes"][i]["estimated_seconds"]
        entry: dict[str, Any] = {"id": scene.id, "estimated_seconds": need}
        scenes_out.append(entry)
        if scene.clip is not None:
            entry["clip"] = "given"
            used.add(str(scene.clip.get("asset_id") or ""))
            continue
        query = req.queries.get(i)
        if not query:
            narration = " ".join(s.text for s in scene.sentences)
            if not narration:
                entry["warnings"] = ["沒有旁白也沒有 query，這個場景沒有素材"]
                warnings.extend(f"{scene.id}：{w}" for w in entry["warnings"])
                continue
            query = [narration[:40]]
            entry["warnings"] = ["沒有 query，拿旁白文字去搜（非英文關鍵字效果差）；"
                                 "建議自己給 query"]
            warnings.extend(f"{scene.id}：{w}" for w in entry["warnings"])
        entry["query"] = query
        candidates = _search_once(client, query, settings.platform, settings, cache, searches)
        best, alternatives, notes = choose(candidates, need, settings, used)
        entry["alternatives"] = alternatives
        entry["warnings"] = [*entry.get("warnings", []), *notes]
        warnings.extend(f"{scene.id}：{n}" for n in notes)
        if best is None:
            msg = "沒有可用的素材（搜不到，或候選的授權都不能用）"
            entry["warnings"].append(msg)
            warnings.append(f"{scene.id}：{msg}")
        else:
            used.add(best["id"])
            entry["chosen"] = dict(_brief(best), why=_why(best, need))
            doc["scenes"][i]["clip"] = {"asset_id": best["id"], "start": 0}
        jobs.progress((i + 1) / total)
    music_report = None
    if req.music_query:
        jobs.stage("music", "、".join(req.music_query))
        need_total = described["estimated_seconds"]
        candidates = _search_once(client, req.music_query, settings.music_platform, settings,
                                  cache, searches)
        best, alternatives, notes = choose(candidates, need_total, settings, set(), music=True)
        music_report = {"query": req.music_query, "alternatives": alternatives,
                        "warnings": list(notes)}
        warnings.extend(f"music：{n}" for n in notes)
        if best is None:
            msg = "沒有可用的背景音樂，成片不會有音樂"
            music_report["warnings"].append(msg)
            warnings.append(f"music：{msg}")
        else:
            music_report["chosen"] = dict(_brief(best), why=_why(best, need_total))
            doc["music"] = {**req.music_extra, "asset_id": best["id"]}
    elif req.music_extra:
        doc["music"] = req.music_extra
    final = manifest_mod.parse(doc, subtitle_langs=cfg.subtitle_langs, burn_in=cfg.burn_subtitles)
    missing = [s.id for s in final.scenes if s.clip is None]
    report = {"short_id": final.id, "estimated_seconds": described["estimated_seconds"],
              "estimate_note": described["estimate_note"], "scenes": scenes_out,
              "music": music_report, "searches": searches, "without_clip": missing,
              "warnings": warnings}
    return {"schema": SCHEMA, "manifest": doc, "report": report}


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """存進工作紀錄的結果：完整 manifest（agent 直接拿去 make）與報告。"""
    return {"short_id": result["report"]["short_id"], "manifest": result["manifest"],
            "report": result["report"]}


# ------------------------------------------------------------------ 檔案

def store_request(cfg: Config, short_id: str, doc: Any) -> Path:
    """HTTP／MCP 送件：把草稿放進作品資料夾，子行程讀它。"""
    root = timeline.short_dir(cfg, short_id)
    root.mkdir(parents=True, exist_ok=True)
    timeline._atomic_json(root / REQUEST_FILE, doc)
    return root / REQUEST_FILE


def store_result(cfg: Config, short_id: str, result: dict[str, Any]) -> Path:
    root = timeline.short_dir(cfg, short_id)
    root.mkdir(parents=True, exist_ok=True)
    timeline._atomic_json(root / PLAN_FILE, result)
    return root / PLAN_FILE


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    """`-o`：把 manifest 寫成檔（先寫 .partial 再改名）。"""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.partial")
    try:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest

