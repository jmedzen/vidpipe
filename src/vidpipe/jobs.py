"""工作紀錄：每次長指令（normalize、concat，之後的 narrate／render）都留下狀態與事件。

CLI、HTTP、MCP 都透過同一個 `JobRecorder` 寫，所以網頁看得到任何入口發起的工作——
agent 在終端機跑 `vidpipe normalize`，你在網頁上一樣看得到進度。

佈局（`work_dir` 底下，沒有資料庫）：

    _jobs/<id>.json          目前狀態，整份原子改寫（寫暫存檔再 rename）
    _jobs/<id>.events.jsonl  只增不減的事件，每行一個 JSON，帶遞增的 seq
    _jobs/<id>.cancel        取消標記（伺服器寫；寫狀態的只有跑工作的那個行程）

id 是 `YYYYMMDD-HHMMSS-xxxxxx`，字串排序就是時間順序。

記錄是「盡力而為」：work 目錄寫不進去時，指令照跑、只是不留紀錄（不讓記錄壞了正事）。
"""

from __future__ import annotations

import contextvars
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import BadInput, NotFound, VidpipeError, from_exception

JOBS_DIR = "_jobs"
ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
STATUSES = ("running", "succeeded", "failed", "cancelled", "interrupted")
# 進度最少隔多久、至少前進多少才寫一次狀態；事件則每 10% 才記一筆
_PROGRESS_EVERY_S = 0.5
_PROGRESS_STEP = 0.02
_EVENT_STEP = 0.10


class Cancelled(VidpipeError):
    """工作被取消（收到 SIGTERM，或使用者按 Ctrl-C）。"""

    code = "cancelled"
    retryable = False


class Conflict(VidpipeError):
    code = "conflict"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def jobs_dir(work_dir: str | Path) -> Path:
    return Path(work_dir) / JOBS_DIR


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _new_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def check_id(job_id: str) -> str:
    """id 要符合固定格式；檔名從這裡來，不能讓 `..` 之類溜進路徑。"""
    if not ID_RE.match(job_id or ""):
        raise BadInput(f"不是合法的工作 id：{job_id!r}", hint="格式 YYYYMMDD-HHMMSS-xxxxxx")
    return job_id


_active: contextvars.ContextVar[JobRecorder | None] = contextvars.ContextVar(
    "vidpipe_active_job", default=None)


def active() -> JobRecorder | None:
    return _active.get()


def stage(name: str, detail: str = "") -> None:
    """目前有工作在記錄就開新階段；沒有就什麼都不做（渲染程式碼不必知道有沒有人在看）。"""
    rec = _active.get()
    if rec:
        rec.stage(name, detail)


def progress(ratio: float) -> None:
    rec = _active.get()
    if rec:
        rec.progress(ratio)


def log(message: str, level: str = "info") -> None:
    rec = _active.get()
    if rec:
        rec.log(message, level)


class JobRecorder:
    """`with JobRecorder(work_dir, "normalize", args={...}) as job:`

    離開時自動記成功／失敗／取消；例外照常往外丟。
    """

    def __init__(self, work_dir: str | Path, kind: str, *, args: dict[str, Any] | None = None,
                 short_id: str | None = None, enabled: bool = True):
        self.kind = kind
        self.enabled = enabled
        self.id = _new_id()
        self._dir = jobs_dir(work_dir)
        self._seq = 0
        self._last_write = 0.0
        self._last_ratio = -1.0
        self._last_event_ratio = -1.0
        self._prev_handler: Any = None
        self._token: contextvars.Token[JobRecorder | None] | None = None
        self.state: dict[str, Any] = {
            "id": self.id, "kind": kind, "short_id": short_id, "status": "running",
            "args": args or {}, "pid": os.getpid(), "host": platform.node(),
            "created_at": _now(), "started_at": _now(), "finished_at": None, "updated_at": _now(),
            "stage": None, "stages": [], "progress": None, "result": None, "error": None,
        }

    # -- 檔案 ---------------------------------------------------------

    @property
    def _state_path(self) -> Path:
        return self._dir / f"{self.id}.json"

    def _write(self) -> None:
        if not self.enabled:
            return
        self.state["updated_at"] = _now()
        try:
            _atomic_write(self._state_path, self.state)
        except OSError:
            self.enabled = False  # 寫不進去就放棄記錄，不影響指令本身

    def _event(self, kind: str, message: str = "", **extra: Any) -> None:
        if not self.enabled:
            return
        self._seq += 1
        line = {"seq": self._seq, "t": round(time.time(), 3), "kind": kind, "message": message,
                **extra}
        try:
            with (self._dir / f"{self.id}.events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            self.enabled = False

    # -- 生命週期 -------------------------------------------------------

    def __enter__(self) -> JobRecorder:
        if self.enabled:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False
        self._token = _active.set(self)
        if threading.current_thread() is threading.main_thread():
            try:
                self._prev_handler = signal.signal(signal.SIGTERM, self._on_sigterm)
            except (ValueError, OSError):
                self._prev_handler = None
        self._write()
        self._event("start", f"開始 {self.kind}")
        return self

    @staticmethod
    def _on_sigterm(_signum: int, _frame: Any) -> None:
        raise Cancelled("工作已取消")

    def __exit__(self, exc_type: Any, exc: BaseException | None, _tb: Any) -> bool:
        if self._prev_handler is not None:
            try:
                signal.signal(signal.SIGTERM, self._prev_handler)
            except (ValueError, OSError):
                pass
        if self._token is not None:
            _active.reset(self._token)
        cancelled = isinstance(exc, (Cancelled, KeyboardInterrupt))
        self._close_stage("succeeded" if exc is None else "cancelled" if cancelled else "failed")
        if exc is None:
            self.state["status"] = "succeeded"
            self.state["progress"] = 1.0
            self._event("end", "完成")
        elif cancelled:
            self.state["status"] = "cancelled"
            self.state["error"] = Cancelled("工作已取消").to_dict()["error"]
            self._event("end", "已取消", level="warn")
        else:
            err = from_exception(exc)
            self.state["status"] = "failed"
            self.state["error"] = err.to_dict()["error"]
            self._event("error", err.message, level="error", code=err.code, hint=err.hint)
        self.state["finished_at"] = _now()
        self._write()
        try:
            self._cancel_marker().unlink(missing_ok=True)
        except OSError:
            pass
        return False  # 例外照常往外丟，CLI 會轉成結構化錯誤

    # -- 記錄 ---------------------------------------------------------

    def _close_stage(self, status: str) -> None:
        if self.state["stages"] and self.state["stages"][-1]["status"] == "running":
            last = self.state["stages"][-1]
            last["status"] = status
            last["finished_at"] = _now()

    def stage(self, name: str, detail: str = "") -> None:
        self._close_stage("succeeded")
        self.state["stages"].append({"name": name, "status": "running", "detail": detail,
                                     "started_at": _now(), "finished_at": None})
        self.state["stage"] = name
        self._last_ratio = self._last_event_ratio = -1.0
        self.state["progress"] = None
        self._event("stage", name, detail=detail)
        self._write()

    def progress(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, ratio))
        now = time.monotonic()
        if ratio < 1.0 and (now - self._last_write < _PROGRESS_EVERY_S
                            or ratio - self._last_ratio < _PROGRESS_STEP):
            return
        self._last_write, self._last_ratio = now, ratio
        self.state["progress"] = round(ratio, 3)
        if ratio - self._last_event_ratio >= _EVENT_STEP or ratio >= 1.0:
            self._last_event_ratio = ratio
            self._event("progress", f"{int(ratio * 100)}%", ratio=round(ratio, 3))
        self._write()

    def log(self, message: str, level: str = "info") -> None:
        self._event("log", message, level=level)

    def set_result(self, result: dict[str, Any]) -> None:
        self.state["result"] = result
        self._write()

    def _cancel_marker(self) -> Path:
        return self._dir / f"{self.id}.cancel"


# ------------------------------------------------------------------ 讀取（伺服器用）

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_vidpipe_process(pid: int) -> bool:
    """pid 現在跑的真的是 vidpipe 嗎？pid 會被重用，不確認就送 SIGTERM 可能殺到無關的行程。"""
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        if cmdline.exists():
            return "vidpipe" in cmdline.read_bytes().decode("utf-8", "replace")
    except OSError:
        return False
    if shutil.which("ps"):
        try:
            out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                                 text=True, timeout=5, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "vidpipe" in out
    return False


def _effective(state: dict[str, Any]) -> dict[str, Any]:
    """寫著 running 但行程已經不在（被 kill -9、機器重開）→ 對外顯示 interrupted。"""
    if state.get("status") == "running" and state.get("host") == platform.node():
        pid = state.get("pid")
        if isinstance(pid, int) and not _pid_alive(pid):
            return {**state, "status": "interrupted"}
    return state


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_jobs(work_dir: str | Path, *, limit: int = 100,
              status: str | None = None) -> list[dict[str, Any]]:
    """新的在前。壞掉或半寫的檔案跳過。"""
    if status is not None and status not in STATUSES:
        raise BadInput(f"不認得的狀態：{status}", hint=f"可用：{', '.join(STATUSES)}")
    root = jobs_dir(work_dir)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        if not ID_RE.match(path.stem):
            continue
        state = _load(path)
        if state is None:
            continue
        state = _effective(state)
        if status is None or state.get("status") == status:
            out.append(state)
        if len(out) >= limit:
            break
    return out


def get_job(work_dir: str | Path, job_id: str) -> dict[str, Any]:
    check_id(job_id)
    state = _load(jobs_dir(work_dir) / f"{job_id}.json")
    if state is None:
        raise NotFound(f"找不到工作：{job_id}")
    return _effective(state)


def read_events(work_dir: str | Path, job_id: str, after: int = 0) -> list[dict[str, Any]]:
    check_id(job_id)
    path = jobs_dir(work_dir) / f"{job_id}.events.jsonl"
    events: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # 正在寫的最後一行
                if isinstance(event, dict) and event.get("seq", 0) > after:
                    events.append(event)
    except OSError:
        pass
    return events


def request_cancel(work_dir: str | Path, job_id: str) -> dict[str, Any]:
    """對還在跑、而且確定是 vidpipe 的行程送 SIGTERM。只在同一台機器上做得到。"""
    state = get_job(work_dir, job_id)
    if state["status"] != "running":
        raise Conflict(f"工作不在執行中（{state['status']}）",
                       hint="只有 running 的工作能取消")
    if state.get("host") != platform.node():
        raise Conflict("這個工作是在別台機器上跑的，從這裡取消不了",
                       hint="到跑它的那台機器上取消")
    pid = state.get("pid")
    if not isinstance(pid, int) or not _is_vidpipe_process(pid):
        raise Conflict("找不到對應的 vidpipe 行程", hint="它可能剛好結束了；重新整理看狀態")
    (jobs_dir(work_dir) / f"{job_id}.cancel").write_text(_now(), encoding="utf-8")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError as exc:
        raise Conflict("行程已經結束", hint="重新整理看狀態") from exc
    return state
