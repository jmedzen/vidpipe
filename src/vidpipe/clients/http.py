"""帶重試的極簡 HTTP client。

- 連線失敗、逾時、5xx、429 才重試（退避 0.5s、1s、2s…）；4xx 重試也不會好，直接回報。
- 對方回 vidgrab 風格的結構化錯誤（`{"ok": false, "error": {...}}`）時，保留它的
  code／retryable／hint，包成 UpstreamError。
- token 只放在 Authorization header，任何錯誤訊息都不帶 header、也不帶 query。
"""

from __future__ import annotations

import time
from typing import Any

import requests

from ..errors import UpstreamError


def _structured(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return err
    if isinstance(err, str):  # 舊式 {"error": "..."}
        return {"message": err}
    # OpenAI 風格 {"error": {"message", "type"}} 已在上面處理；其他形狀忽略
    return {}


class HttpClient:
    def __init__(self, base_url: str, *, name: str, token: str = "",
                 timeout: float = 30.0, retries: int = 2, backoff: float = 0.5,
                 sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self._sleep = sleep
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "vidpipe"
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def request(self, method: str, path: str, *, timeout: float | None = None,
                **kw: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        last: UpstreamError | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(method, url, timeout=timeout or self.timeout, **kw)
            except requests.Timeout:
                last = UpstreamError(f"{self.name} 逾時（{path}）", upstream=self.name,
                                     hint="服務忙或模型還在載入，稍後重試")
            except requests.ConnectionError:
                last = UpstreamError(
                    f"{self.name} 連不上（{self.base_url}）", upstream=self.name,
                    hint=f"確認 {self.name} 有在跑，位址是否正確（設定 {self.name}_url）")
            else:
                if resp.status_code < 400:
                    return resp
                err = _structured(resp)
                detail = err.get("message") or (resp.text or "").strip()[:200]
                retryable = resp.status_code >= 500 or resp.status_code == 429
                if "retryable" in err:
                    retryable = bool(err["retryable"])
                hint = err.get("hint") or ""
                if resp.status_code in (401, 403):
                    hint = hint or f"{self.name} 拒絕了 token：檢查 {self.name}_token"
                last = UpstreamError(
                    f"{self.name} 回 HTTP {resp.status_code}：{detail}".rstrip("：").rstrip(),
                    upstream=self.name, upstream_code=str(err.get("code") or ""),
                    hint=hint, retryable=retryable)
                if not retryable:
                    raise last
            if attempt < self.retries:
                self._sleep(self.backoff * (2 ** attempt))
        assert last is not None
        raise last

    def get_json(self, path: str, **kw: Any) -> Any:
        resp = self.request("GET", path, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamError(f"{self.name} 回的不是 JSON（{path}）", upstream=self.name,
                                retryable=False) from exc
