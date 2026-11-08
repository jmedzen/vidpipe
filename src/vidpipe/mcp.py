"""MCP server：讓 Claude Code 等 MCP client 直接用 vidpipe 做一支 Short。

兩種傳輸，共用同一份工具實作（跟 vidgrab 一樣的做法，刻意不引入 `mcp` 套件）：

- Streamable HTTP：`vidpipe serve` 的 `POST /mcp`（認證與 `/api/*` 相同）
- stdio：`vidpipe mcp`（給本機 client 以子行程啟動）

工具只回 **JSON 與網址，不回影片內容**：成品用 `get_short` 給的 `url`
（`/file/...`，接在 vidpipe 的位址後面）取，同一台機器時也可以用工作結果裡的 `path`。

做片的工作會跑很久（合成旁白、編碼），所以 `make_short`／`run_step` 預設等 `wait` 秒（60），
沒做完就回 job id，之後用 `get_job` 回頭查——跟 vidgrab 的 `grab_clip` 同一個節奏。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import config as config_mod
from . import jobs, shorts
from . import manifest as manifest_mod
from .errors import BadInput, VidpipeError, from_exception
from .version import __version__

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_WAIT = 60.0
MAX_WAIT = 600.0
POLL = 0.5
STEPS = ("narrate", "render", "cover", "credits")     # run_step 能做的單一步驟

INSTRUCTIONS = (
    "做 YouTube Short：先 manifest_guide 看 manifest 怎麼寫 → validate_manifest 檢查 → "
    "make_short 一次做完（旁白、時間軸、渲染、封面、credits）→ get_short 取成品網址。"
    "素材用 vidgrab 的 asset id 或本機路徑；還沒挑素材就先 plan_short（場景給英文 query，"
    "它去搜、依授權與長度與方向挑好，回完整 manifest 與備選）。"
    "回傳的 warnings 要看（素材循環、授權未明、封面沒做成…）。"
)

GUIDE_TAIL = """
重點：
- `scenes` 一個場景一段旁白（`text` 自動分句，或 `sentences` 逐句寫，
  雙語字幕要配 `en`）；沒有旁白的場景要給 `min_duration`。
- `clip` 擇一給 `asset_id`（vidgrab 的素材 id，例如 pixabay:123，
  也可以是片段 id pixabay:123#2-8）或 `path`（伺服器上的檔案）。
  `start` 是素材的第幾秒；只會向 vidgrab 要 start 起、場景那麼長的一段。
  素材不夠長會循環並在 warnings 提醒。
- `music`：`volume` 是音樂相對旁白的響度（1 = 一樣大聲，預設 0.3），
  `duck_under_voice` 讓旁白出聲時自動壓低音樂。
- 成片自動標準化到 -14 LUFS，同時產出封面（`cover.at`、`cover.text`）與素材來源清單 credits.md。
- 不認得的欄位一律報錯（多半是打錯字），訊息帶路徑，例如 `scenes[1].txt`。
- 還沒挑素材：場景給 `query`（英文關鍵字效果最好）代替 `clip`，音樂給 `music.query`，
  用 plan_short 交給 vidpipe 挑；回來的 `manifest` 可直接 make_short，`report` 列出每個場景
  挑了誰、為什麼、前幾名備選（想換就改 `asset_id`）。
- 流程：validate_manifest（不呼叫 TTS）→ make_short。
  只想調整某一步：run_step（narrate／render／cover／credits）。
"""


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


_MANIFEST = {"type": "object",
             "description": "manifest（vidpipe.manifest/1），寫法見 manifest_guide"}
_WAIT = {"type": "number",
         "description": f"最多等幾秒（預設 {int(DEFAULT_WAIT)}、上限 {int(MAX_WAIT)}）；"
                        "沒做完就回 job id，之後用 get_job"}

TOOLS: list[dict[str, Any]] = [
    {"name": "manifest_guide",
     "description": "manifest 的寫法：欄位、範例與注意事項。第一次做片先看這個。",
     "inputSchema": _obj({})},
    {"name": "validate_manifest",
     "description": "檢查 manifest（不呼叫 TTS、不碰素材）：回每個場景的分句、字數與粗估長度；"
                    "寫錯會回錯誤，帶欄位路徑（例如 scenes[1].txt）。",
     "inputSchema": _obj({"manifest": _MANIFEST}, ["manifest"])},
    {"name": "plan_short",
     "description": "草稿 → 搜尋並挑素材與音樂 → 可以直接 make_short 的 manifest。"
                    "草稿就是 manifest，只是場景可以用 query（搜尋關鍵字，英文最好）代替 clip，"
                    "music 也可以給 query。規則：授權明確優先、長度夠、直式優先；"
                    "結果的 report 列出選擇與備選。只搜尋，不呼叫 TTS、不下載。",
     "inputSchema": _obj({"manifest": {"type": "object",
                                       "description": "草稿：manifest 加上 query、search，"
                                                      "見 manifest_guide"},
                          "wait": _WAIT}, ["manifest"])},
    {"name": "make_short",
     "description": "一次做完：合成旁白 → 排時間軸 → 渲染成 9:16 mp4 → 封面 → credits.md。"
                    "一支片同時只能有一個工作。回 job 與 short（檔案網址）；"
                    "沒在 wait 內做完就回 job id，用 get_job 查。",
     "inputSchema": _obj({"manifest": _MANIFEST, "wait": _WAIT}, ["manifest"])},
    {"name": "run_step",
     "description": "只做其中一步：narrate（旁白＋時間軸＋字幕檔）、"
                    "render（用 narrate 的結果渲染）、"
                    "cover（重做封面，可帶 at 秒數與 text 標題）、credits（重做素材來源清單）。"
                    "改過 manifest 要先 narrate 再 render；改封面或清單不必重新渲染。",
     "inputSchema": _obj({"op": {"type": "string", "enum": list(STEPS)},
                          "manifest": _MANIFEST,
                          "at": {"type": "number",
                                 "description": "cover 用：取第幾秒的畫面"},
                          "text": {"type": "string",
                                   "description": "cover 用：封面標題，空字串 = 不放字"},
                          "wait": _WAIT}, ["op", "manifest"])},
    {"name": "get_job",
     "description": "查工作狀態（階段、進度、結果、失敗原因）。"
                    "帶 wait 會等它做完（最多 wait 秒）。",
     "inputSchema": _obj({"id": {"type": "string"}, "wait": _WAIT}, ["id"])},
    {"name": "cancel_job",
     "description": "取消還在跑的工作（已完成的素材與旁白快取會保留）。",
     "inputSchema": _obj({"id": {"type": "string"}}, ["id"])},
    {"name": "list_shorts",
     "description": "所有作品：階段（new／planned／narrated／rendered）、長度、檔案、最近的工作。",
     "inputSchema": _obj({})},
    {"name": "get_short",
     "description": "單一作品的完整狀態：manifest、時間軸摘要、素材出處、"
                    "檔案網址（mp4、封面、credits、字幕）、最近的工作。",
     "inputSchema": _obj({"id": {"type": "string"}}, ["id"])},
    {"name": "check_setup",
     "description": "檢查環境：ffmpeg、vidgrab、TTS 是否就緒；沒就緒會說原因與怎麼修。"
                    "（沒就緒不算錯誤：看 ok 與 checks。）",
     "inputSchema": _obj({"skip": {"type": "array", "items": {"type": "string"},
                                   "description": "略過的項目，例如 [\"vidgrab\", \"tts\"]"}})},
]


# ------------------------------------------------------------------ 工具實作

def _doc(args: dict[str, Any]) -> Any:
    if not isinstance(args.get("manifest"), dict):
        raise BadInput("缺少 manifest（要是 JSON 物件）", hint="寫法見 manifest_guide")
    return args["manifest"]


def _wait_seconds(args: dict[str, Any]) -> float:
    wait = args.get("wait", DEFAULT_WAIT)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
        raise BadInput("wait 要是不小於 0 的數字（秒）")
    return min(float(wait), MAX_WAIT)


def _brief(job: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "kind", "short_id", "status", "stage", "progress", "result", "error",
            "created_at", "finished_at")
    return {k: job.get(k) for k in keys}


def _outcome(cfg: Any, job_id: str, wait: float) -> dict[str, Any]:
    """等工作最多 `wait` 秒，回它現在的樣子。失敗與取消回 ok=False（呼叫端轉成 isError）。"""
    deadline = time.monotonic() + wait
    job = jobs.get_job(cfg.work_dir, job_id)
    while job["status"] == "running" and time.monotonic() < deadline:
        time.sleep(min(POLL, max(0.0, deadline - time.monotonic())))
        job = jobs.get_job(cfg.work_dir, job_id)
    out: dict[str, Any] = {"ok": job["status"] in ("running", "succeeded"),
                           "done": job["status"] != "running", "job": _brief(job)}
    if job["status"] == "running":
        stage = job.get("stage") or "啟動中"
        out["hint"] = f"還在跑（{stage}）；用 get_job 帶 id={job_id} 回頭查，可以設 wait"
    elif job.get("short_id"):
        out["short"] = shorts.summarize(cfg, job["short_id"])
    if not out["ok"] and job.get("error"):
        out["error"] = job["error"]
    return out


def _tool_manifest_guide(args: dict[str, Any]) -> Any:
    return {"ok": True, "schema": manifest_mod.SCHEMA,
            "guide": (manifest_mod.__doc__ or "").strip() + "\n" + GUIDE_TAIL.strip()}


def _tool_validate_manifest(args: dict[str, Any]) -> Any:
    manifest, _ = shorts.parse_document(_doc(args))
    return {"ok": True, **shorts.describe(manifest)}


def _launch(op: str, args: dict[str, Any]) -> Any:
    cfg = config_mod.load()
    wait = _wait_seconds(args)
    at, text = args.get("at"), args.get("text")
    if at is not None and (isinstance(at, bool) or not isinstance(at, (int, float))):
        raise BadInput("at 要是數字（秒）")
    if text is not None and not isinstance(text, str):
        raise BadInput("text 要是字串")
    info = shorts.launch(cfg, op, _doc(args), at=at, text=text)
    return _outcome(cfg, info["job"], wait)


def _tool_plan_short(args: dict[str, Any]) -> Any:
    return _launch("plan", args)


def _tool_make_short(args: dict[str, Any]) -> Any:
    return _launch("make", args)


def _tool_run_step(args: dict[str, Any]) -> Any:
    op = args.get("op")
    if op not in STEPS:
        raise BadInput(f"op 不認得：{op!r}", hint=f"{'、'.join(STEPS)} 其中一個")
    return _launch(op, args)


def _job_id(args: dict[str, Any]) -> str:
    value = args.get("id")
    if not isinstance(value, str):
        raise BadInput("缺少工作 id")
    return value


def _tool_get_job(args: dict[str, Any]) -> Any:
    return _outcome(config_mod.load(), _job_id(args), _wait_seconds({"wait": args.get("wait", 0)}))


def _tool_cancel_job(args: dict[str, Any]) -> Any:
    cfg = config_mod.load()
    job = jobs.request_cancel(cfg.work_dir, _job_id(args))
    return {"ok": True, "signalled": True, "job": _brief(job)}


def _tool_list_shorts(args: dict[str, Any]) -> Any:
    return {"ok": True, "shorts": shorts.list_shorts(config_mod.load())}


def _tool_get_short(args: dict[str, Any]) -> Any:
    sid = args.get("id")
    if not isinstance(sid, str):
        raise BadInput("缺少作品 id")
    return {"ok": True, "short": shorts.detail(config_mod.load(), sid)}


def _tool_check_setup(args: dict[str, Any]) -> Any:
    from . import doctor

    skip = args.get("skip") or []
    if not isinstance(skip, list) or not all(isinstance(x, str) for x in skip):
        raise BadInput("skip 要是字串陣列", hint='例如 ["vidgrab", "tts"]')
    return doctor.run(config_mod.load(), skip)


# 回 ok=False 只是「狀態報告」而不是呼叫失敗的工具
REPORT_ONLY = frozenset({"check_setup"})

HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "manifest_guide": _tool_manifest_guide,
    "validate_manifest": _tool_validate_manifest,
    "plan_short": _tool_plan_short,
    "make_short": _tool_make_short,
    "run_step": _tool_run_step,
    "get_job": _tool_get_job,
    "cancel_job": _tool_cancel_job,
    "list_shorts": _tool_list_shorts,
    "get_short": _tool_get_short,
    "check_setup": _tool_check_setup,
}


def _result(data: Any, *, error: bool = False) -> dict[str, Any]:
    res: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "isError": error,
    }
    if isinstance(data, dict):
        res["structuredContent"] = data
    return res


def call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """執行工具，回 MCP 的 CallToolResult。錯誤用 isError＋結構化內容，不丟例外給 client。"""
    handler = HANDLERS.get(name)
    if handler is None:
        return _result({"ok": False, "error": {"code": "not_found",
                                               "message": f"沒有這個工具：{name}",
                                               "retryable": False,
                                               "hint": "tools/list 看有哪些工具"}}, error=True)
    try:
        data = handler(arguments or {})
    except VidpipeError as exc:
        return _result(exc.to_dict() | {"ok": False}, error=True)
    except Exception as exc:  # noqa: BLE001 - 不丟例外給 client
        return _result(from_exception(exc).to_dict() | {"ok": False}, error=True)
    failed = isinstance(data, dict) and data.get("ok") is False and name not in REPORT_ONLY
    return _result(data, error=failed)


# ------------------------------------------------------------------ JSON-RPC

def rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg: Any) -> dict[str, Any] | None:
    """處理一則 JSON-RPC 訊息。通知（沒有 id）回 None。"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return rpc_error(None, -32600, "Invalid Request")
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return rpc_error(msg_id, -32602, "params 必須是物件")

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "vidpipe", "version": __version__},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        if not isinstance(params.get("name"), str):
            return rpc_error(msg_id, -32602, "tools/call 需要 name")
        args = params.get("arguments")
        if args is not None and not isinstance(args, dict):
            return rpc_error(msg_id, -32602, "arguments 必須是物件")
        return {"jsonrpc": "2.0", "id": msg_id, "result": call_tool(params["name"], args)}
    if "id" not in msg:
        return None  # notifications/initialized、notifications/cancelled …
    return rpc_error(msg_id, -32601, f"Method not found: {method}")


def handle_payload(payload: Any) -> Any | None:
    """HTTP 用：單一訊息或 batch（陣列）。全是通知就回 None（HTTP 202）。"""
    if isinstance(payload, list):
        if not payload:
            return rpc_error(None, -32600, "Invalid Request")
        replies = [r for r in (handle_message(m) for m in payload) if r is not None]
        return replies or None
    return handle_message(payload)


def _is_tool_call(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("method") == "tools/call"


def serve_stdio() -> int:
    """stdio 傳輸：一行一則 JSON-RPC。stdout 只能有協定訊息，其餘（log）全走 stderr。

    `tools/call` 各用一條執行緒跑：make_short 會等好幾十秒，這段時間 client 還要能送
    cancel_job、get_job，不能被前一個呼叫擋住。回覆靠 id 對應，順序不重要。
    """
    real_out = sys.stdout
    sys.stdout = sys.stderr
    out_lock = threading.Lock()
    workers: list[threading.Thread] = []

    def reply_to(payload: Any) -> None:
        reply = handle_payload(payload)
        if reply is not None:
            with out_lock:
                real_out.write(json.dumps(reply, ensure_ascii=False) + "\n")
                real_out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            with out_lock:
                real_out.write(json.dumps(rpc_error(None, -32700, "Parse error")) + "\n")
                real_out.flush()
            continue
        if _is_tool_call(payload):
            worker = threading.Thread(target=reply_to, args=(payload,), daemon=True)
            worker.start()
            workers.append(worker)
        else:
            reply_to(payload)
    for worker in workers:      # client 關了 stdin：來得及回的回完再走
        worker.join(timeout=5)
    return 0
