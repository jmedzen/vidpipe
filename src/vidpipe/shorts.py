"""作品（short）層的操作：描述、一次做完、列出、查看、送出工作、取檔。

CLI（`validate`、`make`）、HTTP API 與之後的 MCP 共用這一層，三邊的行為才會一致。

- `describe`／`make`：不碰 HTTP 的純函式。
- `launch`：HTTP／MCP 送件用。把 manifest 存進 `work_dir/shorts/<id>/manifest.json`，
  開一個 `python -m vidpipe <指令>` 子行程去跑——工作紀錄、進度、取消（SIGTERM）都是 W2
  做好的那一套，所以不需要另一個工作佇列；伺服器重開也不會弄丟正在跑的工作。
- `summarize`／`detail`／`resolve_file`：看狀態與取檔。只認白名單裡的檔名，路徑跳脫不可能。
- `delete`：刪掉一支片的中間檔與成品（共用的旁白快取不動）。
- `update_manifest`：網頁編輯後寫回 manifest；`revision` 防止蓋掉別人剛改的
  （別的視窗、agent、新的 plan）。
- `search_assets`：換素材時向 vidgrab 搜尋，候選依授權、夠不夠長、直式優先排好。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import assets, credits, jobs, planner, render, timeline
from . import config as config_mod
from . import manifest as manifest_mod
from .config import Config
from .errors import BadInput, NotFound, VidpipeError
from .manifest import ID_RE, Manifest

OPS = ("make", "narrate", "render", "cover", "credits", "plan")   # 會建立工作的操作
START_WAIT = 10.0       # 送出工作後，最多等多久讓子行程把工作紀錄建起來

# 一般檔案的 Content-Type；沒列的一律不提供
FILE_TYPES = {
    ".mp4": "video/mp4", ".jpg": "image/jpeg", ".md": "text/markdown; charset=utf-8",
    ".srt": "text/plain; charset=utf-8", ".ass": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".wav": "audio/wav",
}
WORK_FILES = ("manifest.json", "timeline.json", "sources.json", "narration.wav", "subs.srt",
              "subs.ass", "plan.json")
# 成片旁邊的檔案：<id>.mp4、<id>.cover.jpg、<id>.credits.md、<id>.srt
OUT_SUFFIXES = (".mp4", ".cover.jpg", ".credits.md", ".srt")

# 中文旁白大約每秒 4–5 字；只是排鏡頭長度時的粗估，實際以合成後的秒數為準
CHARS_PER_SECOND = 4.5


# ------------------------------------------------------------------ 不碰 HTTP 的部分

def describe(manifest: Manifest) -> dict[str, Any]:
    """`validate` 的結果：每個場景的分句、字數、粗估長度。不呼叫 TTS、不碰素材。"""
    scenes = []
    for scene in manifest.scenes:
        chars = sum(len(s.text) for s in scene.sentences)
        speech = chars / CHARS_PER_SECOND
        scenes.append({"id": scene.id, "sentences": [s.text for s in scene.sentences],
                       "chars": chars, "min_duration": scene.min_duration,
                       "estimated_seconds": round(max(scene.min_duration, speech), 1),
                       "clip": scene.clip, "fit": scene.fit})
    return {"short_id": manifest.id, "scenes": scenes,
            "estimated_seconds": round(sum(s["estimated_seconds"] for s in scenes), 1),
            "estimate_note": f"以每秒 {CHARS_PER_SECOND:g} 字粗估；實際長度以 narrate 的結果為準"}


def make(manifest: Manifest, raw: Any, cfg: Config,
         out: str | Path | None = None) -> dict[str, Any]:
    """旁白 → 時間軸 → 渲染，一個工作做完。回 `render` 的結果，多一個 `narrate` 摘要。"""
    narrated = timeline.run(manifest, raw, cfg)
    rendered = render.run(manifest, raw, cfg, out)
    rendered["narrate"] = {"total": narrated["total"], "tts": narrated["tts"],
                           "warnings": narrated["warnings"]}
    rendered["warnings"] = [*narrated["warnings"], *rendered["warnings"]]
    return rendered


def parse_document(doc: Any, cfg: Config | None = None) -> tuple[Manifest, Config]:
    """HTTP／MCP 收到的 manifest（已經是 JSON 物件）→ (Manifest, 套用 voice 的設定)。"""
    cfg = cfg or config_mod.load()
    manifest = manifest_mod.parse(doc, subtitle_langs=cfg.subtitle_langs,
                                  burn_in=cfg.burn_subtitles)
    return manifest, manifest_mod.apply_voice(manifest, cfg)


# ------------------------------------------------------------------ 檔案與狀態

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_short_id(short_id: str) -> str:
    if not ID_RE.match(short_id or ""):
        raise BadInput(f"不是合法的作品 id：{short_id!r}",
                       hint="小寫英數、底線、連字號，最長 64 字")
    return short_id


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def files_of(cfg: Config, short_id: str) -> list[dict[str, Any]]:
    """這支片現有的成品與中間檔：`url` 是 `/file/...`，跨機器、容器取檔用。"""
    check_short_id(short_id)
    found: list[dict[str, Any]] = []
    out = Path(cfg.out_dir)
    for suffix in OUT_SUFFIXES:
        path = out / f"{short_id}{suffix}"
        if path.is_file():
            found.append({"area": "out", "name": path.name, "size_bytes": path.stat().st_size,
                          "url": f"/file/{short_id}/out/{path.name}"})
    root = timeline.short_dir(cfg, short_id)
    for name in WORK_FILES:
        path = root / name
        if path.is_file():
            found.append({"area": "work", "name": name, "size_bytes": path.stat().st_size,
                          "url": f"/file/{short_id}/work/{name}"})
    return found


def resolve_file(cfg: Config, short_id: str, area: str, name: str) -> tuple[Path, str]:
    """(檔案路徑, Content-Type)。只認 `files_of` 會列出的那幾種檔名。"""
    check_short_id(short_id)
    if area == "out":
        allowed = any(name == f"{short_id}{s}" for s in OUT_SUFFIXES)
        base = Path(cfg.out_dir)
    elif area == "work":
        allowed = name in WORK_FILES
        base = timeline.short_dir(cfg, short_id)
    else:
        raise NotFound(f"沒有這個檔案區：{area}", hint="area 是 out 或 work")
    path = base / name
    if not allowed or not path.is_file():
        raise NotFound(f"找不到檔案：{short_id}/{area}/{name}",
                       hint="`GET /api/short/<id>` 會列出現有的檔案")
    return path, FILE_TYPES.get(path.suffix, "application/octet-stream")


class StaleManifest(jobs.Conflict):
    """編輯時的 revision 已經不是現在的 manifest：不蓋掉對方，請呼叫端重新載入。"""

    code = "stale_manifest"


def revision_of(doc: Any) -> str | None:
    """manifest 內容的短雜湊：編輯時帶回來，內容已經變了就知道不能直接蓋掉。"""
    if not isinstance(doc, dict):
        return None
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _outdated(cfg: Config, doc: Any, source: str | None, tl: Any, sources: Any) -> bool | None:
    """manifest（或音色、字幕設定）比上次做出來的新嗎——也就是要不要重做。

    還沒做過旁白、或 manifest 讀不懂就回 None。時間軸已經重算、成片還是舊的也算過期；
    還沒有成片（只做了旁白）就沒有東西過期。
    """
    if not isinstance(tl, dict) or source not in ("manifest", "plan") or doc is None:
        return None
    try:
        manifest, effective = parse_document(doc, cfg)
    except VidpipeError:
        return None
    if tl.get("fingerprint") != timeline.fingerprint(manifest, doc, effective):
        return True
    return isinstance(sources, dict) and sources.get("fingerprint") != tl.get("fingerprint")


def _stage(files: list[dict[str, Any]]) -> str:
    names = {f["name"] for f in files}
    if any(n.endswith(".mp4") for n in names):
        return "rendered"
    if "timeline.json" in names:
        return "narrated"
    if planner.PLAN_FILE in names:
        return "planned"
    return "new"


def _current_manifest(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """這支片現在「算數」的 manifest，與它的來源（`manifest`／`plan`／`draft`）。

    `manifest.json` 是最後一次送去做（narrate、render、make）的版本；`plan` 不碰它，只留
    `plan.json`。所以 plan 比 manifest.json 新（或還沒有 manifest.json）就用 plan 挑好的那份；
    連 plan 都還沒跑完（或失敗了）就退回送進去的草稿，讓列表上至少看得到標題與場景數。
    """
    made = root / "manifest.json"
    planned = root / planner.PLAN_FILE
    doc = _read_json(made)
    if isinstance(doc, dict) and not (
            planned.is_file() and planned.stat().st_mtime > made.stat().st_mtime):
        return doc, "manifest"
    plan = _read_json(planned)
    if isinstance(plan, dict) and isinstance(plan.get("manifest"), dict):
        return plan["manifest"], "plan"
    draft = _read_json(root / planner.REQUEST_FILE)
    if isinstance(draft, dict):
        return draft, "draft"
    return (doc, "manifest") if isinstance(doc, dict) else (None, None)


def _job_brief(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in ("id", "kind", "status", "stage", "progress", "created_at",
                                    "finished_at")}


def _credits_brief(short_id: str, title: str, sources: Any) -> dict[str, Any] | None:
    """素材與授權的數量（列表上的「有 N 個授權要確認」用）；還沒渲染過就是 None。"""
    if not isinstance(sources, dict):
        return None
    summary = credits.build(short_id, title, sources)[1]
    return {"assets": summary["assets"],
            "attribution_required": len(summary["attribution_required"]),
            "unclear": len(summary["unclear"])}


def summarize(cfg: Config, short_id: str, latest: dict[str, dict[str, Any]] | None = None
              ) -> dict[str, Any]:
    root = timeline.short_dir(cfg, short_id)
    doc = _current_manifest(root)[0] or {}
    tl = _read_json(root / "timeline.json") or {}
    files = files_of(cfg, short_id)
    mtimes = [(root / f["name"]).stat().st_mtime for f in files if f["area"] == "work"]
    job = (latest or {}).get(short_id)
    return {"id": short_id, "title": doc.get("title") or "", "stage": _stage(files),
            "scenes": len(doc.get("scenes") or []), "total": tl.get("total"),
            "files": files, "updated_at": _iso(max(mtimes)) if mtimes else None,
            "credits": _credits_brief(short_id, doc.get("title") or "",
                                      _read_json(root / "sources.json")),
            "last_job": _job_brief(job) if job else None}


def _latest_jobs(cfg: Config) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for job in jobs.list_jobs(cfg.work_dir, limit=200):   # 新的在前：每支片留第一筆
        sid = job.get("short_id")
        if sid and sid not in latest:
            latest[sid] = job
    return latest


def list_shorts(cfg: Config) -> list[dict[str, Any]]:
    """work_dir/shorts/ 底下的作品，最近動過的在前。"""
    root = Path(cfg.work_dir) / "shorts"
    if not root.is_dir():
        return []
    latest = _latest_jobs(cfg)
    items = [summarize(cfg, p.name, latest) for p in root.iterdir()
             if p.is_dir() and ID_RE.match(p.name)]
    return sorted(items, key=lambda i: i["updated_at"] or "", reverse=True)


def detail(cfg: Config, short_id: str) -> dict[str, Any]:
    check_short_id(short_id)
    root = timeline.short_dir(cfg, short_id)
    if not root.is_dir():
        raise NotFound(f"沒有這支片：{short_id}", hint="`GET /api/shorts` 列出所有作品")
    tl = _read_json(root / "timeline.json")
    sources = _read_json(root / "sources.json")
    summary = summarize(cfg, short_id, _latest_jobs(cfg))
    recent = [_job_brief(j) for j in jobs.list_jobs(cfg.work_dir, limit=200)
              if j.get("short_id") == short_id][:10]
    manifest, source = _current_manifest(root)
    plan = _read_json(root / planner.PLAN_FILE)
    return {**summary, "manifest": manifest, "manifest_source": source,
            "manifest_revision": revision_of(manifest),
            "outdated": _outdated(cfg, manifest, source, tl, sources),
            "plan": plan.get("report") if isinstance(plan, dict) else None,
            "timeline": None if tl is None else {
                "total": tl.get("total"), "total_frames": tl.get("total_frames"),
                "fps": tl.get("fps"), "scenes": tl.get("scenes"), "warnings": tl.get("warnings"),
                "narration": tl.get("narration"), "subtitles": tl.get("subtitles")},
            "sources": sources, "jobs": recent}


# ------------------------------------------------------------------ 送出工作

def _wait_for_job(cfg: Config, proc: subprocess.Popen[bytes], errfile: Any) -> str:
    """等子行程把工作紀錄建起來，回 job id；它在那之前就結束了就回報原因。"""
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        for job in jobs.list_jobs(cfg.work_dir, limit=30):
            if job.get("pid") == proc.pid:
                return str(job["id"])
        if proc.poll() is not None:
            errfile.seek(0)
            tail = errfile.read().decode("utf-8", "replace").strip().splitlines()[-3:]
            why = " | ".join(tail) or f"子行程結束（{proc.returncode}）"
            raise VidpipeError(f"工作沒有啟動：{why}", hint="用 `vidpipe doctor` 檢查環境")
        time.sleep(0.05)
    raise VidpipeError("工作啟動逾時（子行程沒有留下工作紀錄）",
                       hint="伺服器可能太忙；稍後看 `GET /api/jobs`，工作也許已經開始")


def _assert_idle(cfg: Config, short_id: str) -> None:
    """同一支片同時只能有一個工作；有的話回 409。"""
    for job in jobs.list_jobs(cfg.work_dir, limit=200, status="running"):
        if job.get("short_id") == short_id:
            raise jobs.Conflict(f"{short_id} 已經有工作在跑（{job['kind']}：{job['id']}）",
                                hint="等它做完，或 `POST /api/job/<id>/cancel` 取消")


def draft_from_body(body: dict[str, Any]) -> dict[str, Any]:
    """`POST /api/plan` 的另一種本體：純文字腳本（網頁貼上去的那種）加幾個選項。

    `{"script": "…", "id": "taipei-night-01", "title": "…", "music": "calm piano",
    "allow_unknown_license": false}`；腳本格式與 `vidpipe plan script.txt` 一樣。
    """
    def text(key: str, *, required: bool = False) -> str | None:
        value = body.get(key)
        if value is None or value == "":
            if required:
                raise BadInput(f"缺少 {key}", hint="本體格式：{\"script\": \"…\", \"id\": \"…\"}")
            return None
        if not isinstance(value, str):
            raise BadInput(f"{key} 要是字串")
        return value

    allow = body.get("allow_unknown_license", False)
    if not isinstance(allow, bool):
        raise BadInput("allow_unknown_license 要是 true 或 false")
    short_id = check_short_id(text("id", required=True) or "")
    doc = planner.parse_script(text("script", required=True) or "", short_id=short_id,
                               title=text("title") or "")
    return planner.apply_options(doc, music=text("music"), allow_unknown_license=allow,
                                 platform=text("platform"), orientation=text("orientation"))


def update_manifest(cfg: Config, short_id: str, doc: Any, revision: Any) -> dict[str, Any]:
    """網頁編輯：驗證過就寫回 `manifest.json`（之後 `make` 只會重做改到的旁白與場景）。

    `revision` 要是 `GET /api/short/<id>` 給的 `manifest_revision`；現在的 manifest 已經不是
    那一份（有人改過、或重新 plan 過）就回 409，不蓋掉。作品 id 改不了；有工作在跑時不給改。
    """
    check_short_id(short_id)
    root = timeline.short_dir(cfg, short_id)
    if not root.is_dir():
        raise NotFound(f"沒有這支片：{short_id}", hint="`GET /api/shorts` 列出所有作品")
    current, source = _current_manifest(root)
    if source not in ("manifest", "plan"):
        raise BadInput("這支片還沒有可以改的 manifest", hint="先挑素材（plan），或 make 一次")
    _assert_idle(cfg, short_id)
    if not isinstance(revision, str) or not revision:
        raise BadInput("缺少 revision", hint="用 `GET /api/short/<id>` 回的 manifest_revision")
    if revision != revision_of(current):
        raise StaleManifest("這份 manifest 已經被改過了（別的視窗、agent，或重新 plan 過）",
                            hint="重新載入再改一次")
    manifest, _ = parse_document(doc, cfg)
    if manifest.id != short_id:
        raise BadInput(f"manifest 的 id（{manifest.id}）要跟這支片一樣（{short_id}）",
                       hint="作品 id 改不了")
    timeline.store_manifest(cfg, short_id, doc)
    return {"short_id": short_id, "manifest_revision": revision_of(doc)}


SEARCH_LIMIT = 24
SEARCH_MAX = 60
SEARCH_TIMEOUT = 60.0        # 互動用的搜尋，不像 plan 可以等五分鐘
SEARCH_KEYS = {"query", "target", "platform", "orientation", "allow_unknown_license", "limit"}


def _search_keywords(value: Any) -> list[str]:
    """`"a, b"` 或 `["a", "b"]` → 關鍵字清單；空的不行。"""
    if isinstance(value, str):
        value = [part for part in re.split(r"[,，、\n]", value)]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise BadInput("query 要是字串（用逗號分開）或字串清單",
                       hint='例："night market, street food"')
    found = [x.strip() for x in value if x.strip()]
    if not found:
        raise BadInput("query 不能是空的", hint="給一到幾個搜尋關鍵字（英文效果最好）")
    return found[:8]


def search_assets(cfg: Config, short_id: str, body: Any, *, client: Any = None) -> dict[str, Any]:
    """換素材：依關鍵字向 vidgrab 搜尋，回排好的候選（不改任何檔案）。

    `body`：`{"query": "a, b", "target": "scene:<id>" | "music"}`，另可帶 `platform`、
    `orientation`（預設 any：橫式也列出來，只是排在直式後面）、`allow_unknown_license`、`limit`。
    排序跟 `plan` 同一套（授權 > 夠不夠長 > 直式優先），但已經用在別的場景的不會被擠掉，
    只標 `used_in`；現在這個場景用的標 `current`。授權不明的預設不列，數量在 `skipped_license`。
    """
    check_short_id(short_id)
    root = timeline.short_dir(cfg, short_id)
    if not root.is_dir():
        raise NotFound(f"沒有這支片：{short_id}", hint="`GET /api/shorts` 列出所有作品")
    doc, source = _current_manifest(root)
    if source not in ("manifest", "plan") or doc is None:
        raise BadInput("這支片還沒有可以換素材的 manifest", hint="先挑素材（plan），或 make 一次")
    if not isinstance(body, dict):
        raise BadInput("本體要是 JSON 物件", hint='{"query": "night market", "target": "scene:s1"}')
    extra = sorted(set(body) - SEARCH_KEYS)
    if extra:
        raise BadInput(f"不認得的欄位：{', '.join(extra)}",
                       hint=f"可用：{', '.join(sorted(SEARCH_KEYS))}")
    keywords = _search_keywords(body.get("query"))
    manifest, _ = parse_document(doc, cfg)
    described = describe(manifest)
    target = body.get("target")
    music = target == "music"
    current = ""
    used_in: dict[str, list[str]] = {}
    if music:
        need = described["estimated_seconds"]
        current = str((doc.get("music") or {}).get("asset_id") or "")
    elif isinstance(target, str) and target.startswith("scene:"):
        scene_id = target[len("scene:"):]
        ids = [scene.id for scene in manifest.scenes]
        if scene_id not in ids:
            raise BadInput(f"這支片沒有場景 {scene_id}", hint=f"場景：{', '.join(ids)}")
        index = ids.index(scene_id)
        need = described["scenes"][index]["estimated_seconds"]
        current = str((manifest.scenes[index].clip or {}).get("asset_id") or "")
        for scene in manifest.scenes:
            other = str((scene.clip or {}).get("asset_id") or "")
            if other and scene.id != scene_id:
                used_in.setdefault(other, []).append(scene.id)
    else:
        raise BadInput("target 要是 scene:<場景 id> 或 music", hint='例："scene:s1"')

    default = planner.Settings().music_platform if music else planner.Settings().platform
    platform = body.get("platform", default)
    allowed = planner.MUSIC_PLATFORMS if music else planner.PLATFORMS
    if platform not in allowed:
        raise BadInput(f"不認得的 platform：{platform!r}", hint=f"可用：{', '.join(allowed)}")
    orientation = body.get("orientation", "any")
    if orientation not in planner.ORIENTATIONS:
        raise BadInput(f"不認得的 orientation：{orientation!r}",
                       hint=f"可用：{', '.join(planner.ORIENTATIONS)}")
    limit = body.get("limit", SEARCH_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= SEARCH_MAX:
        raise BadInput(f"limit 要是 1–{SEARCH_MAX} 的整數")
    allow_unknown = body.get("allow_unknown_license", False)
    if not isinstance(allow_unknown, bool):
        raise BadInput("allow_unknown_license 要是 true 或 false")

    settings = planner.Settings(platform=platform, orientation=orientation,
                                allow_unknown_license=allow_unknown, per_keyword=12,
                                timeout=SEARCH_TIMEOUT)
    client = client or assets.make_client(cfg)
    found = client.search(keywords, platform=platform, orientation=orientation,
                          per_keyword=settings.per_keyword, timeout=settings.timeout)
    candidates = list(found["candidates"])
    ranked, _ = planner.rank(candidates, need, settings, set(), music=music)
    ranked.sort(key=lambda c: bool(used_in.get(c["id"])))        # 用在別處的排後面；其餘維持原排序
    return {"short_id": short_id, "target": target, "query": keywords, "platform": platform,
            "need": need, "total": len(candidates),
            "skipped_license": planner.skipped_count(candidates, settings),
            "candidates": [dict(planner._brief(c), why=planner._why(c, need, music=music),
                                enough=planner.long_enough(c, need),
                                used_in=used_in.get(c["id"], []), current=c["id"] == current)
                           for c in ranked[:limit]]}


def delete(cfg: Config, short_id: str) -> dict[str, Any]:
    """刪掉這支片的中間檔（`work/shorts/<id>`）與成品（out 裡的 mp4、封面、credits、srt）。

    旁白快取是各片共用的，不動；工作紀錄也留著。有工作在跑時不給刪（409）。
    """
    check_short_id(short_id)
    root = timeline.short_dir(cfg, short_id)
    out = Path(cfg.out_dir)
    outputs = [out / f"{short_id}{suffix}" for suffix in OUT_SUFFIXES]
    if not root.is_dir() and not any(p.is_file() for p in outputs):
        raise NotFound(f"沒有這支片：{short_id}", hint="`GET /api/shorts` 列出所有作品")
    _assert_idle(cfg, short_id)
    removed = [f"{f['area']}/{f['name']}" for f in files_of(cfg, short_id)]
    shutil.rmtree(root, ignore_errors=True)
    for path in outputs:
        path.unlink(missing_ok=True)
    return {"short_id": short_id, "deleted": True, "removed": removed}


def launch(cfg: Config, op: str, doc: Any, *, at: float | None = None,
           text: str | None = None) -> dict[str, Any]:
    """驗證 manifest、存進作品資料夾、開子行程跑 `vidpipe <op>`，回 job id（工作在背景跑）。

    `plan` 收的是草稿（場景可以只有 `query`），驗證規則見 `planner.parse_request`。
    """
    if op not in OPS:
        raise BadInput(f"不認得的操作：{op}", hint=f"可用：{', '.join(OPS)}")
    if op == "plan":
        short_id = planner.parse_request(doc, cfg).manifest.id
    else:
        short_id = parse_document(doc, cfg)[0].id
    _assert_idle(cfg, short_id)
    path = (planner.store_request(cfg, short_id, doc) if op == "plan"
            else timeline.store_manifest(cfg, short_id, doc))
    cmd = [sys.executable, "-m", "vidpipe", op, str(path)]
    if op == "cover":
        if at is not None:
            cmd += ["--at", repr(float(at))]
        if text is not None:
            cmd += ["--text", text]
    env = {**os.environ, "VIDPIPE_RECORD_JOBS": "true"}   # 沒有紀錄就沒有 job 可看
    with tempfile.TemporaryFile() as errfile:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=errfile, env=env, start_new_session=True)
        threading.Thread(target=proc.wait, daemon=True).start()   # 收屍，免得留 zombie
        job_id = _wait_for_job(cfg, proc, errfile)
    return {"short_id": short_id, "op": op, "job": job_id, "status_url": f"/api/job/{job_id}"}
