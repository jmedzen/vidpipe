"""vidgrab client：健康檢查、用 id 找素材、抓一段、取檔。

vidgrab 的契約（見它的 README「給 AI agent 用」）：
- `GET /api/find/<id>`：asset id（`pixabay:123`）或片段 id（`pixabay:123#2-8`）→
  `{project, item, clip?}`；找不到回 404。
- `POST /api/batch`：對專案裡的某支素材抓指定區段，**冪等**（同一個區段抓過就不再抓，
  `new_clips` 為空）、`wait: true` 在請求裡等到做完。
- `GET /file/<專案>/<路徑>`：取檔（支援 Range）；跨機器、容器時用這個，不要用 `path`。
"""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path
from typing import Any

from ..errors import UpstreamError
from .http import HttpClient


class VidgrabClient(HttpClient):
    def __init__(self, base_url: str, token: str = "", **kw: Any):
        super().__init__(base_url, name="vidgrab", token=token, **kw)

    def version(self) -> dict[str, Any]:
        """`/api/version` 是公開的 healthcheck（不需 token）。"""
        return self.get_json("/api/version", timeout=10)

    def check_auth(self) -> None:
        """打一個需要 token 的端點；token 不對會丟 UpstreamError（401）。"""
        self.get_json("/api/projects", timeout=10)

    def find(self, asset_id: str) -> dict[str, Any] | None:
        """用 asset id 或片段 id 找回素材；素材庫裡沒有就回 None。"""
        path = "/api/find/" + urllib.parse.quote(asset_id, safe="")
        try:
            return self.get_json(path, timeout=30)
        except UpstreamError as exc:
            if exc.status == 404:
                return None
            raise

    def grab(self, project: str, asset_id: str, start: float, end: float, *,
             timeout: float = 900.0, poll: float = 2.0) -> dict[str, Any]:
        """抓 `asset_id` 的 start–end 秒（冪等），回 `vidgrab.batch/1`。

        `frames: false`：vidpipe 只要片段，不需要 vidgrab 順便抽畫面。
        """
        body = {"project": project, "asset_ids": [asset_id], "ranges": [[start, end]],
                "frames": False, "wait": True, "timeout": timeout}
        resp = self.request("POST", "/api/batch", json=body, timeout=timeout + 60)
        data = resp.json()
        if resp.status_code == 202 and data.get("job"):  # 比等待上限還久：改成輪詢
            return self._wait_job(str(data["job"]), timeout, poll)
        return data

    def _wait_job(self, job_id: str, timeout: float, poll: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get_json(f"/api/job/{urllib.parse.quote(job_id)}", timeout=30)
            if job.get("done"):
                if job.get("error"):
                    raise UpstreamError(f"vidgrab 的批次失敗：{job['error']}", upstream="vidgrab",
                                        retryable=True)
                return job.get("result") or {}
            self._sleep(poll)
        raise UpstreamError(f"vidgrab 的批次超過 {timeout:g} 秒還沒做完", upstream="vidgrab",
                            retryable=True, hint="素材太大或網路慢；稍後重試（已抓好的不會重抓）")

    def download(self, url_path: str, dest: str | Path, *, timeout: float = 600.0) -> int:
        """把 `/file/...` 取回來寫成 dest（先寫 .partial 再改名），回位元組數。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.partial")
        total = 0
        resp = self.request("GET", url_path, timeout=timeout, stream=True)
        try:
            with tmp.open("wb") as out:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    out.write(chunk)
                    total += len(chunk)
            tmp.replace(dest)
        finally:
            resp.close()
            tmp.unlink(missing_ok=True)
        return total
