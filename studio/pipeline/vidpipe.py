"""呼叫 vidpipe(CLI,stdout 只有 JSON)。成片、旁白、字幕、封面、credits 都交給它。"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .config import abspath


class VidpipeError(Exception):
    def __init__(self, message: str, *, code: str = "", retryable: bool = False, hint: str = ""):
        super().__init__(message)
        self.code, self.retryable, self.hint = code, retryable, hint


def _env(cfg: dict) -> dict:
    env = dict(os.environ)
    vp = cfg["vidpipe"]
    env.update(vp.get("env", {}))
    env["VIDPIPE_WORK_DIR"] = str(Path(cfg["work"]) / "vidpipe")
    env["VIDPIPE_OUT_DIR"] = str(Path(cfg["work"]) / "vidpipe-out")
    if vp.get("path_prepend"):
        env["PATH"] = os.path.expanduser(vp["path_prepend"]) + os.pathsep + env.get("PATH", "")
    return env


def run(cfg: dict, *args: str, timeout: float | None = None, retries: int = 2,
        log: Path | None = None) -> dict:
    """跑一個 vidpipe 指令,回它的 JSON。retryable 的錯誤會重試(旁白與場景有快取,重跑只補缺的)。"""
    cmd = [abspath(cfg, cfg["vidpipe"]["bin"]), *args]
    last: VidpipeError | None = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=_env(cfg),
                                  timeout=timeout or cfg["vidpipe"].get("timeout", 3600),
                                  check=False)
        except subprocess.TimeoutExpired:
            last = VidpipeError(f"vidpipe {args[0]} 逾時", code="timeout", retryable=True)
        else:
            if log:
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n$ vidpipe {' '.join(args)}\n{proc.stderr[-4000:]}\n")
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                last = VidpipeError(f"vidpipe {args[0]} 沒有回 JSON:{proc.stderr[-300:]}",
                                    code="internal", retryable=True)
            else:
                if data.get("ok"):
                    return data
                err = data.get("error") or {}
                last = VidpipeError(err.get("message", "vidpipe 失敗"), code=err.get("code", ""),
                                    retryable=bool(err.get("retryable")), hint=err.get("hint", ""))
        if not last.retryable or attempt == retries:
            raise last
        time.sleep(5 * (attempt + 1))
    raise last  # pragma: no cover


def work_dir(cfg: dict, short_id: str) -> Path:
    return Path(cfg["work"]) / "vidpipe" / "shorts" / short_id
