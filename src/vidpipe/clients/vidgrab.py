"""vidgrab client。目前只有健康檢查；搜尋與下載在階段 4–5 接上。"""

from __future__ import annotations

from typing import Any

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
