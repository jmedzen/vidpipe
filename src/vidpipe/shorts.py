"""作品（short）層的操作：描述、一次做完、列出、查看、送出工作、取檔。

CLI（`validate`、`make`）、HTTP API 與之後的 MCP 共用這一層，三邊的行為才會一致。

- `describe`／`make`：不碰 HTTP 的純函式。
- `launch`：HTTP／MCP 送件用。把 manifest 存進 `work_dir/shorts/<id>/manifest.json`，
  開一個 `python -m vidpipe <指令>` 子行程去跑——工作紀錄、進度、取消（SIGTERM）都是 W2
  做好的那一套，所以不需要另一個工作佇列；伺服器重開也不會弄丟正在跑的工作。
- `summarize`／`detail`／`resolve_file`：看狀態與取檔。只認白名單裡的檔名，路徑跳脫不可能。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import jobs, planner, render, timeline
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


def _stage(files: list[dict[str, Any]]) -> str:
    names = {f["name"] for f in files}
    if any(n.endswith(".mp4") for n in names):
        return "rendered"
    if "timeline.json" in names:
        return "narrated"
    return "new"


def _job_brief(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in ("id", "kind", "status", "stage", "progress", "created_at",
                                    "finished_at")}


def summarize(cfg: Config, short_id: str, latest: dict[str, dict[str, Any]] | None = None
              ) -> dict[str, Any]:
    root = timeline.short_dir(cfg, short_id)
    doc = _read_json(root / "manifest.json") or {}
    tl = _read_json(root / "timeline.json") or {}
    files = files_of(cfg, short_id)
    mtimes = [(root / f["name"]).stat().st_mtime for f in files if f["area"] == "work"]
    job = (latest or {}).get(short_id)
    return {"id": short_id, "title": doc.get("title") or "", "stage": _stage(files),
            "scenes": len(doc.get("scenes") or []), "total": tl.get("total"),
            "files": files, "updated_at": _iso(max(mtimes)) if mtimes else None,
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
    return {**summary, "manifest": _read_json(root / "manifest.json"),
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
    for job in jobs.list_jobs(cfg.work_dir, limit=200, status="running"):
        if job.get("short_id") == short_id:
            raise jobs.Conflict(f"{short_id} 已經有工作在跑（{job['kind']}：{job['id']}）",
                                hint="等它做完，或 `POST /api/job/<id>/cancel` 取消")
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
