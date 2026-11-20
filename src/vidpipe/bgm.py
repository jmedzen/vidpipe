"""本機背景音樂曲庫：把音檔放進資料夾就能用，依標籤挑、輪流用，不會每支片都同一首。

資料夾（設定 `bgm_dir`，預設 `./bgm`）的長相：

    bgm/
      古琴/            ← 子資料夾名稱就是標籤（可以多層：bgm/舒緩/古琴/… = 舒緩＋古琴）
        流水.mp3
        流水.json      ← 選填：同名 .json 寫授權、出處、更多標籤
      明亮/
        晨光.m4a
      _停用/           ← 底線或點開頭的資料夾不會被挑到

同名 `.json`（全部選填）：

    {"title": "流水", "author": "某某", "license": "free-attribution",
     "credit": "某某《流水》（CC BY 4.0）— https://…", "url": "https://…",
     "tags": ["舒緩", "夜晚"], "start": 0, "volume": 0.22, "enabled": true}

- `license`：`own`（自己做的）、`free`（CC0／可自由使用，不必標註）、`free-attribution`（要標註，
  例如 CC BY），沒寫就是「未標示」：還是可以用，但成片的 credits 會打 ⚠，公開前要自己確認。
- **挑歌**：先依標籤篩（多個標籤是「任一個符合」；也可以直接寫檔名指定一首），再挑「最久沒用過」的，
  同樣久的用作品 id 的雜湊決定。同一支片重做會拿到同一首（記在 `bgm/.vidpipe/history.json`）。
- 不下載任何東西：曲庫裡只有你自己放進去的檔案。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import ffmpeg
from .config import Config
from .errors import BadInput, NotFound, VidpipeError
from .manifest import FILE_LICENSES

CONTENT_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
                 ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
                 ".opus": "audio/ogg"}
AUDIO_EXTS = tuple(CONTENT_TYPES)
LICENSES = (*FILE_LICENSES, "unknown")
STATE_DIR = ".vidpipe"
MAX_TRACKS = 2000
MAX_HISTORY = 500
DEFAULT_VOLUME = 0.22
META_KEYS = {"title", "author", "license", "credit", "url", "tags", "start", "volume", "enabled",
             "note"}
README_NAME = "說明.txt"
README_TEXT = """把背景音樂放在這個資料夾，vidpipe 出片時會自動挑一首、輪流用。

- 子資料夾名稱就是標籤，例如 古琴/、笛簫/、明亮/、夜晚/；出片時可以指定標籤。
- 音檔旁邊放同名 .json 可以寫授權與出處（也可以在網頁的「背景音樂」頁填）：
  {"license": "free-attribution", "credit": "作者《曲名》（CC BY 4.0）— 網址", "tags": ["舒緩"]}
  license：own＝自己做的、free＝CC0／免標註、free-attribution＝要標註（CC BY 之類）
- 底線或點開頭的資料夾（例如 _停用/）不會被挑到。
- 只放你確定可以用的音樂；沒標授權的會在 credits 打 ⚠ 提醒你。
"""

_LOCK = threading.Lock()


# ------------------------------------------------------------------ 資料夾與檔案

def root(cfg: Config) -> Path:
    return Path(cfg.bgm_dir or "bgm").expanduser().resolve()


def _state(cfg: Config) -> Path:
    return root(cfg) / STATE_DIR


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.partial")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def meta_path(audio: Path) -> Path:
    return audio.with_suffix(".json")


def init(cfg: Config) -> dict[str, Any]:
    """建立曲庫資料夾與說明檔（已經有就不動）。"""
    base = root(cfg)
    created = not base.is_dir()
    base.mkdir(parents=True, exist_ok=True)
    readme = base / README_NAME
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    return {"dir": str(base), "created": created}


def _files(base: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "_")))
        for name in sorted(filenames):
            if not name.startswith(".") and Path(name).suffix.lower() in AUDIO_EXTS:
                out.append(Path(dirpath) / name)
                if len(out) >= MAX_TRACKS:
                    return out
    return out


def _durations(cfg: Config, files: list[Path]) -> dict[str, float | None]:
    """每個檔案的長度；以 路徑＋大小＋修改時間 快取在 .vidpipe/probe.json。"""
    cache_file = _state(cfg) / "probe.json"
    cache = _read_json(cache_file, {})
    fresh: dict[str, Any] = {}
    out: dict[str, float | None] = {}
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        key = f"{f}|{st.st_size}|{int(st.st_mtime)}"
        if key in cache:
            fresh[key] = cache[key]
        else:
            try:
                info = ffmpeg.probe(f)
                fresh[key] = info["duration"] if info["has_audio"] else None
            except VidpipeError:
                fresh[key] = None
        out[str(f)] = fresh[key]
    if fresh != cache:
        try:
            _write_json(cache_file, fresh)
        except OSError:
            pass                      # 唯讀的資料夾也要能列
    return out


def _clean_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = raw.replace("，", ",").replace("、", ",").split(",")
    if not isinstance(raw, list):
        return []
    return [t.strip() for t in raw if isinstance(t, str) and t.strip()]


def _credit_text(t: dict[str, Any]) -> str:
    if t.get("credit"):
        return str(t["credit"])
    parts = [f"《{t['title']}》"]
    if t.get("author"):
        parts.insert(0, str(t["author"]))
    text = "".join(parts)
    if t.get("url"):
        text += f" — {t['url']}"
    return text


def scan(cfg: Config) -> list[dict[str, Any]]:
    """曲庫裡的每一首：id（相對路徑）、標籤、授權、長度、用過幾次。資料夾不存在回空清單。"""
    base = root(cfg)
    if not base.is_dir():
        return []
    files = _files(base)
    durations = _durations(cfg, files)
    history = _history(cfg)
    uses: dict[str, list[str]] = {}
    for item in history["plays"]:
        uses.setdefault(item["id"], []).append(item["short"])
    tracks = []
    for f in files:
        rel = f.relative_to(base)
        meta = _read_json(meta_path(f), {})
        if not isinstance(meta, dict):
            meta = {}
        folder_tags = list(rel.parts[:-1])
        tags = list(dict.fromkeys(folder_tags + _clean_tags(meta.get("tags"))))
        license_kind = meta.get("license") if meta.get("license") in LICENSES else "unknown"
        track: dict[str, Any] = {
            "id": rel.as_posix(), "path": str(f), "name": f.stem,
            "title": str(meta.get("title") or f.stem), "author": str(meta.get("author") or ""),
            "tags": tags, "folder_tags": folder_tags,
            "license": license_kind, "credit": str(meta.get("credit") or ""),
            "url": str(meta.get("url") or ""),
            "start": _num(meta.get("start"), 0.0, hi=36000),
            "volume": _num(meta.get("volume"), DEFAULT_VOLUME, hi=2),
            "enabled": meta.get("enabled", True) is not False,
            "note": str(meta.get("note") or ""),
            "duration": durations.get(str(f)),
            "has_meta": meta_path(f).is_file(),
            "uses": len(uses.get(rel.as_posix(), [])),
            "last_used_by": (uses.get(rel.as_posix()) or [None])[-1],
        }
        track["credit_text"] = _credit_text(track)
        tracks.append(track)
    return tracks


def _num(value: Any, default: float, *, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value) if 0 <= value <= hi else default


def tags_of(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """所有標籤與各有幾首（只算會被挑到的）。"""
    count: dict[str, int] = {}
    for t in tracks:
        if t["enabled"] and t["duration"]:
            for tag in t["tags"]:
                count[tag] = count.get(tag, 0) + 1
    ranked = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"tag": k, "count": v} for k, v in ranked]


def describe(cfg: Config) -> dict[str, Any]:
    base = root(cfg)
    tracks = scan(cfg)
    return {"dir": str(base), "exists": base.is_dir(), "tracks": tracks, "tags": tags_of(tracks),
            "usable": sum(1 for t in tracks if t["enabled"] and t["duration"])}


def find(cfg: Config, track_id: str) -> Path:
    """曲庫裡的檔案（用相對路徑指定）；`..`、絕對路徑、不是音檔都當作沒有。"""
    base = root(cfg)
    if not isinstance(track_id, str) or not track_id or "\0" in track_id:
        raise NotFound("沒有這首背景音樂")
    path = (base / track_id).resolve()
    try:
        rel = path.relative_to(base)
    except ValueError:
        raise NotFound(f"沒有這首背景音樂：{track_id}") from None
    if (any(p.startswith(".") for p in rel.parts) or path.suffix.lower() not in AUDIO_EXTS
            or not path.is_file()):
        raise NotFound(f"沒有這首背景音樂：{track_id}")
    return path


def update(cfg: Config, track_id: str, changes: Any) -> dict[str, Any]:
    """改一首的同名 .json（標籤、授權、出處、起點、音量、停用）。回更新後的那一首。"""
    path = find(cfg, track_id)
    if not isinstance(changes, dict):
        raise BadInput("changes 要是物件")
    unknown = sorted(set(changes) - META_KEYS)
    if unknown:
        raise BadInput(f"不認得的欄位：{', '.join(unknown)}",
                       hint=f"可用：{', '.join(sorted(META_KEYS))}")
    meta = _read_json(meta_path(path), {})
    if not isinstance(meta, dict):
        meta = {}
    for key, value in changes.items():
        if key == "tags":
            if not isinstance(value, (list, str)):
                raise BadInput("tags 要是字串或字串清單")
            folder = list(Path(track_id).parts[:-1])
            value = [t for t in dict.fromkeys(_clean_tags(value)) if t not in folder]
        elif key == "license":
            if value not in LICENSES:
                raise BadInput(f"license 不認得：{value!r}", hint=f"可用：{', '.join(LICENSES)}")
        elif key in ("start", "volume"):
            hi = 36000 if key == "start" else 2
            number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not number or not 0 <= value <= hi:
                raise BadInput(f"{key} 要是 0–{hi:g} 的數字")
        elif key == "enabled":
            if not isinstance(value, bool):
                raise BadInput("enabled 要是 true 或 false")
        elif not isinstance(value, str):
            raise BadInput(f"{key} 要是字串")
        else:
            value = value.strip()
        if value in ("", []) or (key == "license" and value == "unknown") or \
                (key == "enabled" and value is True):
            meta.pop(key, None)       # 預設值不寫進檔案，保持乾淨
        else:
            meta[key] = value
    if meta.get("license") == "free-attribution" and not (meta.get("credit") or meta.get("author")):
        raise BadInput("要標註的音樂（free-attribution）要寫作者或 credit",
                       hint="例：作者《曲名》（CC BY 4.0）— 網址")
    if meta:
        _write_json(meta_path(path), meta)
    else:
        meta_path(path).unlink(missing_ok=True)
    return next(t for t in scan(cfg) if t["id"] == Path(path).relative_to(root(cfg)).as_posix())


# ------------------------------------------------------------------ 挑歌

def _history(cfg: Config) -> dict[str, Any]:
    data = _read_json(_state(cfg) / "history.json", {})
    if not isinstance(data, dict):
        data = {}
    assigned = data.get("assigned") if isinstance(data.get("assigned"), dict) else {}
    plays = [p for p in data.get("plays") or []
             if isinstance(p, dict) and "id" in p and "short" in p]
    return {"assigned": assigned, "plays": plays}


def _unit(*parts: str) -> float:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def matching(tracks: list[dict[str, Any]], wanted: list[str]) -> list[dict[str, Any]]:
    """可以挑的曲目：啟用、讀得到長度；有給標籤就留「任一個標籤符合」的。

    標籤也可以直接是一首的 id、檔名或曲名（指定那一首）。
    """
    usable = [t for t in tracks if t["enabled"] and t["duration"]]
    if not wanted:
        return usable
    keys = {w.casefold() for w in wanted}
    named = [t for t in usable if {t["id"].casefold(), t["name"].casefold(),
                                    t["title"].casefold()} & keys]
    if named:
        return named
    return [t for t in usable if {x.casefold() for x in t["tags"]} & keys]


def choose(tracks: list[dict[str, Any]], short_id: str, history: dict[str, Any]
           ) -> dict[str, Any]:
    """挑最久沒用過的；同樣久（含都沒用過）用作品 id 的雜湊決定，不同作品會分散開。"""
    last: dict[str, int] = {}
    for i, play in enumerate(history["plays"]):
        last[play["id"]] = i
    return min(tracks, key=lambda t: (last.get(t["id"], -1), _unit(short_id, t["id"])))


def music_spec(track: dict[str, Any], *, volume: float | None = None,
               start: float | None = None, duck: bool = True) -> dict[str, Any]:
    """一首曲目 → manifest 的 `music`（本機檔案＋授權宣告）。"""
    spec: dict[str, Any] = {"path": track["path"],
                            "start": track["start"] if start is None else start,
                            "volume": track["volume"] if volume is None else volume,
                            "duck_under_voice": duck}
    if track["license"] != "unknown":
        spec["license"] = track["license"]
        spec["credit"] = track["credit_text"]
    return spec


def pick(cfg: Config, short_id: str, tags: Any = None, *, record: bool = True,
         warnings: list[str] | None = None) -> dict[str, Any] | None:
    """替一支片挑一首。曲庫是空的回 None（呼叫端可以退回別的來源）。

    回 `{"track": 曲目, "music": manifest 的 music, "reused": 是否沿用這支片上次那首}`。
    標籤沒有對到任何一首時，從整個曲庫挑並加一條警告。
    """
    warnings = warnings if warnings is not None else []
    wanted = _clean_tags(tags)
    tracks = scan(cfg)
    usable = matching(tracks, [])
    if not usable:
        return None
    candidates = matching(tracks, wanted)
    if not candidates:
        warnings.append(f"背景音樂曲庫沒有標籤「{'、'.join(wanted)}」的曲目，"
                        f"改從全部 {len(usable)} 首挑")
        candidates = usable
    with _LOCK:
        history = _history(cfg)
        previous = history["assigned"].get(short_id)
        chosen = next((t for t in candidates if t["id"] == previous), None)
        reused = chosen is not None
        if chosen is None:
            chosen = choose(candidates, short_id, history)
        if record and not reused:
            history["assigned"][short_id] = chosen["id"]
            history["plays"] = [p for p in history["plays"] if p["short"] != short_id]
            history["plays"].append({"id": chosen["id"], "short": short_id,
                                     "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            history["plays"] = history["plays"][-MAX_HISTORY:]
            keep = {p["short"] for p in history["plays"]}
            history["assigned"] = {k: v for k, v in history["assigned"].items() if k in keep}
            try:
                _write_json(_state(cfg) / "history.json", history)
            except OSError:
                warnings.append("背景音樂的輪替紀錄寫不進曲庫資料夾（唯讀？），下次可能挑到同一首")
    if chosen["license"] == "unknown":
        warnings.append(f"背景音樂「{chosen['title']}」沒有標示授權：公開前請確認，"
                        "或在「背景音樂」頁填上授權與出處")
    return {"track": chosen, "music": music_spec(chosen), "reused": reused}
