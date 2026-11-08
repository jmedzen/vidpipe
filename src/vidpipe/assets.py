"""素材取得：manifest 的 `clip` → 一個本機影片檔，加上之後 credits 要用的出處資料。

`clip` 有兩種寫法：

- `{"path": "/a/b.mp4", "start": 3}`：本機檔案，`start` 是檔案裡的第幾秒。
- `{"asset_id": "pixabay:123", "start": 3}`：vidgrab 的素材。`start` 是**整支影片**的第幾秒；
  vidpipe 只向 vidgrab 要 `start` 到 `start + 場景長度` 這一段（多留 0.5 秒），不下載整支。
  - asset id 也可以是片段 id（`pixabay:123#2-8`）：那個檔案本身就是裁好的片段，
    `start` 改成相對於片段檔的開頭，不再向 vidgrab 要新的區段。
  - 素材庫裡已經有一個涵蓋 `[start, start+長度]` 的片段（或整支）就直接用，不重抓。

取回的檔案放在 `work_dir/cache/assets/`（檔名由片段 id 與大小決定，同一個片段不重複取）。
vidgrab 跟 vidpipe 在同一台機器、而且檔案讀得到時直接用那個路徑，不複製；
不同機器或容器就走 `/file/...` 下載。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ffmpeg, jobs
from .clients import VidgrabClient
from .config import Config
from .errors import BadInput, NotFound, UpstreamError

MARGIN = 0.5      # 向 vidgrab 多要的秒數：ffprobe 的長度本來就有幾十毫秒的誤差
TOLERANCE = 0.05  # 判斷「已有的片段涵蓋這一段」時容許的誤差


@dataclass(frozen=True)
class Resolved:
    path: str                 # 本機影片檔
    offset: float             # 從這個檔案的第幾秒開始用
    credit: dict[str, Any] = field(default_factory=dict)  # 出處（給 credits.md）


def cache_dir(cfg: Config) -> Path:
    return Path(cfg.work_dir) / "cache" / "assets"


def make_client(cfg: Config) -> VidgrabClient:
    return VidgrabClient(cfg.vidgrab_url, cfg.vidgrab_token, timeout=60.0)


def _credit_from_item(item: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "asset", "asset_id": item.get("id") or clip.get("asset_id") or "",
            "clip_id": clip.get("id") or "", "title": item.get("title") or "",
            "author": item.get("author") or "", "author_url": item.get("author_url") or "",
            "webpage_url": item.get("webpage_url") or "", "license": item.get("license") or {},
            "credit_hint": item.get("credit_hint") or ""}


# ------------------------------------------------------------------ 找到涵蓋的片段

def _need_end(item: dict[str, Any], start: float, duration: float) -> float:
    """這個場景要用到素材的第幾秒為止；素材不夠長就以素材結尾為準（剩下的由渲染補長）。"""
    total = float(item.get("duration") or 0)
    return min(start + duration, total) if total else start + duration


def _covering(item: dict[str, Any], start: float,
              end: float) -> tuple[dict[str, Any], float] | None:
    """素材庫裡已經有的片段，哪一個涵蓋 [start, end]？回 (片段, 在片段檔裡的起點)。

    優先選最短的（少下載一點）；整支（`is_full`）涵蓋所有範圍。
    """
    total = float(item.get("duration") or 0)
    best: tuple[float, dict[str, Any], float] | None = None
    for clip in item.get("clips") or []:
        if not clip.get("exists"):
            continue
        if clip.get("is_full"):
            if total and end > total + TOLERANCE:
                continue
            span, offset = total or float("inf"), start
        else:
            c0, c1 = clip.get("start"), clip.get("end")
            if c0 is None or c1 is None or start < c0 - TOLERANCE or end > c1 + TOLERANCE:
                continue
            span, offset = float(c1) - float(c0), max(0.0, start - float(c0))
        if best is None or span < best[0]:
            best = (span, clip, offset)
    return None if best is None else (best[1], best[2])


# ------------------------------------------------------------------ 取檔

def _safe_name(clip: dict[str, Any]) -> str:
    cid = str(clip.get("id") or clip.get("path") or "clip")
    slug = re.sub(r"[^0-9A-Za-z]+", "_", cid).strip("_")[:40] or "clip"
    digest = hashlib.sha256(cid.encode("utf-8")).hexdigest()[:10]
    ext = Path(str(clip.get("url") or clip.get("path") or "").split("?")[0]).suffix or ".mp4"
    return f"{slug}-{digest}-{int(clip.get('size_bytes') or 0)}{ext}"


def _local_copy(clip: dict[str, Any]) -> str | None:
    """vidgrab 回的路徑在這台機器上讀得到、大小也對，就直接用。"""
    path = clip.get("path")
    if not path:
        return None
    p = Path(path)
    try:
        if p.is_file() and (not clip.get("size_bytes") or p.stat().st_size == clip["size_bytes"]):
            return str(p)
    except OSError:
        pass
    return None


def _fetch_file(clip: dict[str, Any], cfg: Config, client: VidgrabClient) -> str:
    local = _local_copy(clip)
    if local:
        return local
    url = clip.get("url")
    if not url:
        raise UpstreamError(
            f"vidgrab 沒有提供 {clip.get('id') or '這個片段'} 的取檔網址", upstream="vidgrab",
            retryable=False,
            hint="片段檔可能被刪除或搬離素材庫；重抓這個片段，或在 manifest 改用 clip.path")
    dest = cache_dir(cfg) / _safe_name(clip)
    if not dest.is_file():
        jobs.log(f"向 vidgrab 取檔：{clip.get('id') or url}")
        client.download(str(url), dest)
    return str(dest)


# ------------------------------------------------------------------ 解析 manifest 的 clip

def _check_media(path: str, where: str, kind: str) -> None:
    info = ffmpeg.probe(path)
    if kind == "audio":
        if not info["has_audio"]:
            raise BadInput(f"{where}：{Path(path).name} 沒有音訊軌",
                           hint="music 要指向音樂檔，或有音訊軌的影片檔")
    elif not info["has_video"]:
        raise BadInput(f"{where}：{Path(path).name} 沒有影像軌", hint="clip 要指向影片檔")


def _resolve_path(clip: dict[str, Any], where: str, kind: str = "video") -> Resolved:
    path = Path(str(clip["path"])).expanduser()
    if not path.is_file():
        raise NotFound(f"{where}：找不到素材檔 {path}",
                       hint="路徑相對於執行 vidpipe 的目錄；用絕對路徑最穩（Docker 要掛載進去）")
    _check_media(str(path), where, kind)
    return Resolved(str(path), float(clip.get("start", 0.0)),
                    {"kind": "file", "path": str(path.resolve())})


def _grab(client: VidgrabClient, project: str, item: dict[str, Any], asset_id: str,
          start: float, duration: float) -> tuple[dict[str, Any], float]:
    total = float(item.get("duration") or 0)
    if total and start >= total - 0.1:
        raise BadInput(f"{asset_id}：start={start:g} 已經超過素材長度 {total:g}s",
                       hint="縮小 start，或讓場景循環播放（fit 之外的 fill 會自動補長）")
    end = start + duration + MARGIN
    if total:
        end = min(end, total)
    jobs.log(f"向 vidgrab 抓 {asset_id} 的 {start:g}–{end:g} 秒")
    result = client.grab(project, asset_id, start, end)
    row = (result.get("results") or [{}])[0]
    if row.get("error"):
        raise UpstreamError(f"vidgrab 抓不到 {asset_id}：{row['error']}", upstream="vidgrab",
                            retryable=True, hint="稍後重試；授權或網址失效時先 vidgrab refresh")
    if result.get("timed_out"):
        raise UpstreamError(f"vidgrab 抓 {asset_id} 超時", upstream="vidgrab", retryable=True,
                            hint="已抓好的會保留，重跑只補缺的")
    fresh = row.get("new_clips") or []
    if fresh:
        clip = fresh[0]
        return clip, max(0.0, start - float(clip.get("start") or start))
    # 冪等：這個區段早就抓過，所以沒有 new_clips——回頭從素材庫找
    again = client.find(asset_id)
    hit = _covering(again["item"], start, _need_end(again["item"], start, duration)) \
        if again else None
    if hit is None:
        raise UpstreamError(f"vidgrab 說 {asset_id} 的區段抓好了，但素材庫裡找不到那個片段",
                            upstream="vidgrab", retryable=True,
                            hint="vidgrab 的素材庫可能被改動；重跑一次")
    return hit


def _resolve_asset(clip: dict[str, Any], duration: float, cfg: Config,
                   client: VidgrabClient, where: str, kind: str = "video") -> Resolved:
    asset_id = str(clip["asset_id"])
    start = float(clip.get("start", 0.0))
    hit = client.find(asset_id)
    if hit is None:
        raise NotFound(f"{where}：vidgrab 的素材庫裡沒有 {asset_id}",
                       hint="先用 vidgrab 搜尋並放進專案（search／pick），"
                            "或在 manifest 改用 clip.path")
    item = hit["item"]
    if hit.get("clip"):  # 片段 id：檔案本身就是片段，start 相對於它
        found, offset = hit["clip"], start
        if not found.get("exists"):
            raise NotFound(f"{where}：vidgrab 說 {asset_id} 的檔案不見了",
                           hint="vidgrab refresh 或重抓這個片段")
    else:
        covered = _covering(item, start, _need_end(item, start, duration))
        found, offset = covered if covered else _grab(
            client, hit["project"], item, asset_id, start, duration)
    path = _fetch_file(found, cfg, client)
    _check_media(path, where, kind)
    return Resolved(path, offset, _credit_from_item(item, found))


def resolve(clip: dict[str, Any] | None, duration: float, cfg: Config, *, where: str = "clip",
            make: Callable[[Config], VidgrabClient] = make_client) -> Resolved | None:
    """manifest 的 `clip` → 本機檔案。沒有 `clip`（純色／只有旁白的場景）回 None。

    `duration` 是這個場景的長度（秒），用來決定向 vidgrab 要多長的一段。
    """
    if not clip:
        return None
    if "path" in clip:
        return _resolve_path(clip, where)
    return _resolve_asset(clip, duration, cfg, make(cfg), where)


def resolve_audio(music: dict[str, Any], duration: float, cfg: Config, *, where: str = "music",
                  make: Callable[[Config], VidgrabClient] = make_client) -> Resolved:
    """manifest 的 `music` → 本機音訊檔；規則跟影片素材一樣（`start` 是音樂的第幾秒）。

    `duration` 是整支片的長度，用來決定向 vidgrab 要多長的一段。音樂庫的素材
    （Openverse／Jamendo）與「只要聲音」抓下來的 m4a 都走這裡；有音訊軌的影片檔也行。
    """
    if "path" in music:
        return _resolve_path(music, where, "audio")
    return _resolve_asset(music, duration, cfg, make(cfg), where, "audio")
