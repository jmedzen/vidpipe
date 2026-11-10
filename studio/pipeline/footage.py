"""畫面素材:主題 → vidgrab 素材池 → 每個場景一段。

使用者決定素材用 vidgrab 的 YouTube/B 站來源。這裡不逐場景搜尋(YouTube 用旁白關鍵字搜會搜到
談話節目),而是每個「畫面主題」維護一個素材池(vidgrab 專案 `vo-<主題>`),場景從池裡挑:
授權明確(free、free-attribution)優先,同一支片盡量不重複,起點依場景錯開。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LICENSE_RANK = {"free": 0, "free-attribution": 1, "check": 2, "unknown": 3}


class FootageError(Exception):
    pass


class Vidgrab:
    def __init__(self, base: str, token: str = "", timeout: float = 30):
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout

    def _req(self, path: str, body: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode() if body is not None else None
        last: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.base + path, data, headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    raise FootageError(f"vidgrab {path} 回 {exc.code}") from None
                last = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
            time.sleep(2 * (attempt + 1))
        raise FootageError(f"vidgrab 連不上({path}):{last}")

    def search(self, name: str, keywords: list[str], options: dict, wait: float = 600) -> None:
        job = self._req("/api/search", {"keywords": keywords, "name": name, **options})["job"]
        deadline = time.time() + wait
        while time.time() < deadline:
            state = self._req(f"/api/job/{job}")
            if state.get("done"):
                if state.get("error"):
                    raise FootageError(f"vidgrab 搜尋失敗({name}):{state['error']}")
                return
            time.sleep(3)
        raise FootageError(f"vidgrab 搜尋逾時({name})")

    def items(self, project: str) -> list[dict]:
        data = self._req("/api/vault?project=" + urllib.parse.quote(project))
        projects = data.get("projects") or [data]
        return [it for p in projects for it in p.get("items", [])]


def load_library(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def usable(item: dict, exclude: list[str], allow_unknown: bool, min_seconds: float = 40) -> bool:
    title = (item.get("title") or "").lower()
    if any(word.lower() in title for word in exclude):
        return False
    if (item.get("duration") or 0) < min_seconds:
        return False
    kind = (item.get("license") or {}).get("kind") or "unknown"
    return kind in ("free", "free-attribution") or allow_unknown


def pool(vg: Vidgrab, library: dict, theme: str, cache_dir: Path, *, allow_unknown: bool,
         refresh: bool = False) -> list[dict]:
    """主題的素材池(授權好的排前面)。結果快取在 cache_dir/<theme>.json。"""
    if theme not in library["themes"]:
        raise FootageError(f"不認得的畫面主題:{theme}")
    cache = cache_dir / f"{theme}.json"
    if cache.is_file() and not refresh:
        items = json.loads(cache.read_text(encoding="utf-8"))
    else:
        items, seen = [], set()
        for i, query in enumerate(library["themes"][theme]):     # 每組關鍵字一個 vidgrab 專案
            project = f"vo-{theme}" if i == 0 else f"vo-{theme}-{i}"
            try:
                found = vg.items(project)
            except FootageError:
                found = []
            if not found:
                try:
                    vg.search(project, [query], library["search"])
                    found = vg.items(project)
                except FootageError:
                    found = []      # 這組關鍵字沒搜到東西:看其他組
            for it in found:
                if it["id"] not in seen:
                    seen.add(it["id"])
                    items.append(it)
        keep = ("id", "title", "author", "duration", "license", "width", "height",
                "webpage_url", "credit_hint")
        items = [{k: it.get(k) for k in keep} for it in items]
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    good = [it for it in items if usable(it, library.get("exclude_title", []), allow_unknown)]
    good.sort(key=lambda it: (LICENSE_RANK.get((it.get("license") or {}).get("kind"), 3),
                              -(it.get("height") or 0)))
    return good


def _unit(*parts: str) -> float:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def assign(scenes: list[dict], pools: dict[str, list[dict]], run_id: str,
           banned: set[str] | None = None) -> list[dict]:
    """每個場景 → {asset_id, start, item}。同一支片先用沒用過的素材;起點落在片長的 12%–70%。

    scenes: [{"id", "visual", "seconds"}];banned 是品質檢查退回的素材 id。
    """
    used: dict[str, int] = {}
    out = []
    for sc in scenes:
        cands = [it for it in pools.get(sc["visual"], []) if it["id"] not in (banned or set())]
        if not cands:
            raise FootageError(f"場景 {sc['id']} 的主題 {sc['visual']} 沒有可用素材")
        best_rank = LICENSE_RANK.get((cands[0].get("license") or {}).get("kind"), 3)
        tier = [it for it in cands
                if LICENSE_RANK.get((it.get("license") or {}).get("kind"), 3) == best_rank]
        last = out[-1]["asset_id"] if out else None      # 相鄰鏡頭不用同一支(除非只剩它)
        tier.sort(key=lambda it: (it["id"] == last, used.get(it["id"], 0),
                                  _unit(run_id, sc["id"], it["id"])))
        item = tier[0]
        n = used.get(item["id"], 0)
        used[item["id"]] = n + 1
        duration = float(item.get("duration") or 0)
        room = max(0.0, duration * 0.58 - sc["seconds"])
        frac = (_unit(run_id, item["id"]) + n * 0.29) % 1.0   # 重複使用同一支時錯開起點
        start = round(duration * 0.12 + room * frac, 1)
        out.append({"scene": sc["id"], "asset_id": item["id"], "start": start, "item": item})
    return out
