"""結構化錯誤。

跟 vidgrab 同一套格式，agent 對兩邊用同一份處理邏輯：

    {"ok": false, "error": {"code", "message", "retryable", "hint"}}

`retryable` 讓 agent 知道該重試還是該換方法。
"""

from __future__ import annotations

from typing import Any


class VidpipeError(Exception):
    code = "internal"
    retryable = False
    hint = ""

    def __init__(self, message: str, *, hint: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint
        if retryable is not None:
            self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "hint": self.hint,
            },
        }


class BadInput(VidpipeError):
    code = "bad_input"


class NotFound(VidpipeError):
    code = "not_found"


class ConfigError(VidpipeError):
    code = "bad_config"


class DependencyMissing(VidpipeError):
    """缺 ffmpeg 之類的外部程式。"""

    code = "dependency_missing"


class UpstreamError(VidpipeError):
    """vidgrab / audio.cpp 回了錯或連不上。通常重試有用。"""

    code = "upstream_error"
    retryable = True


class Timeout(VidpipeError):
    code = "timeout"
    retryable = True


def from_exception(exc: BaseException) -> VidpipeError:
    """任何例外都轉成結構化錯誤；不認得的當 internal 且可重試一次。"""
    if isinstance(exc, VidpipeError):
        return exc
    if isinstance(exc, KeyboardInterrupt):
        return VidpipeError("使用者中斷", retryable=True)
    err = VidpipeError(f"{type(exc).__name__}: {exc}", retryable=True)
    err.code = "internal"
    return err
