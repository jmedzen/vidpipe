"""測試用的假 vidgrab：依它的契約（find／batch／job／file）回應，記錄每個請求。"""

from __future__ import annotations

import json
import urllib.parse


class FakeVidgrab:
    """依 vidgrab 的契約（find／batch／job／file）回應；記錄每個請求。"""

    def __init__(self, server, video_bytes: bytes):
        self.server = server
        self.video = video_bytes
        self.items: dict[str, dict] = {}
        self.batches: list[dict] = []
        self.downloads: list[str] = []
        self.headers: list[dict] = []
        self.on_batch = self._default_batch
        self.jobs: list[dict] = []     # 輪詢用：依序回
        self.searches: list[dict] = []
        self.search_handler = lambda body: []        # 搜尋請求 → 候選清單（dict）
        self._found: dict[str, list[dict]] = {}      # 搜尋工作 id → 候選
        self.search_error = ""                       # 非空就讓搜尋工作失敗
        server.handler = self.handle

    # -- 素材庫 -------------------------------------------------------
    def add(self, asset_id="pixabay:1", *, duration=20.0, clips=()):
        item = {"id": asset_id, "title": "Night market", "author": "Ann",
                "author_url": "https://p/ann", "webpage_url": f"https://p/{asset_id}",
                "duration": duration, "license": {"kind": "free", "label": "可自由使用",
                                                  "name": "Pixabay License"},
                "credit_hint": "Ann（Pixabay）", "clips": list(clips)}
        self.items[asset_id] = item
        return item

    @staticmethod
    def candidate(asset_id, *, duration=20.0, width=1080, height=1920, kind="free",
                  label=None, title=None):
        """`/api/vault?project=…` 裡一支候選素材（還沒下載任何片段）。"""
        labels = {"free": "可自由使用", "free-attribution": "可用需標註", "check": "需確認授權",
                  "unknown": "未知"}
        return {"id": asset_id, "platform": asset_id.split(":")[0], "title": title or asset_id,
                "author": "Ann", "author_url": "https://p/ann",
                "webpage_url": f"https://p/{asset_id}", "duration": duration, "width": width,
                "height": height, "license": {"kind": kind, "label": label or labels[kind],
                                              "name": ""},
                "credit_hint": "Ann（Pixabay）", "clips": []}

    @staticmethod
    def clip(asset_id, start, end, *, exists=True, path="", size=0):
        cid = f"{asset_id}#{start:g}-{end:g}" if start is not None else f"{asset_id}#full"
        return {"id": cid, "asset_id": asset_id, "start": start, "end": end,
                "is_full": start is None, "exists": exists, "path": path, "size_bytes": size,
                "url": f"/file/proj/{urllib.parse.quote(cid, safe='')}.mp4"}

    def _default_batch(self, body):
        """真的「抓」：在素材底下新增一個片段。"""
        asset_id = body["asset_ids"][0]
        start, end = body["ranges"][0]
        clip = self.clip(asset_id, start, end)
        self.items[asset_id]["clips"].append(clip)
        return 200, {"schema": "vidgrab.batch/1", "project": body["project"], "timed_out": False,
                     "results": [{"asset_id": asset_id, "new_clips": [clip], "error": None}]}

    # -- HTTP ---------------------------------------------------------
    def handle(self, method, path, headers, body):
        self.headers.append(headers)
        if path.startswith("/api/find/"):
            needle = urllib.parse.unquote(path[len("/api/find/"):])
            asset_id = needle.split("#")[0]
            item = self.items.get(asset_id)
            if item is None:
                return 404, {"error": f"找不到：{needle}", "hint": "…"}
            if "#" in needle:
                clip = next((c for c in item["clips"] if c["id"] == needle), None)
                if clip is None:
                    return 404, {"error": f"找不到：{needle}"}
                return 200, {"project": "proj", "item": item, "clip": clip}
            return 200, {"project": "proj", "item": item}
        if path == "/api/search" and method == "POST":
            req = json.loads(body)
            self.searches.append(req)
            job_id = f"search{len(self.searches)}"
            self._found[job_id] = self.search_handler(req)
            return 200, {"job": job_id}
        if path.startswith("/api/job/search"):
            job_id = path[len("/api/job/"):]
            if self.search_error:
                return 200, {"id": job_id, "done": True, "error": self.search_error,
                             "result": None}
            return 200, {"id": job_id, "done": True, "error": None,
                         "result": {"project": f"proj-{job_id}",
                                    "count": len(self._found[job_id])}}
        if path.startswith("/api/vault?") and method == "GET":
            name = urllib.parse.parse_qs(path.split("?", 1)[1])["project"][0]
            return 200, {"name": name, "items": self._found[name.removeprefix("proj-")]}
        if path == "/api/batch" and method == "POST":
            req = json.loads(body)
            self.batches.append(req)
            return self.on_batch(req)
        if path.startswith("/api/job/"):
            return 200, self.jobs.pop(0)
        if path.startswith("/file/"):
            self.downloads.append(urllib.parse.unquote(path))
            return 200, self.video
        return 404, {"error": "no route"}
