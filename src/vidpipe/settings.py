"""網頁設定頁的後端：讀（連同每項設定的來源）、改（驗證後寫回設定檔）、測試連線。

沿用既有的設定機制，不另開一套：寫的就是 `config.py` 讀的那個 JSON 設定檔（`vidpipe.json`，
`VIDPIPE_CONFIG` 可指定），優先序也不變——環境變數 ＞ 設定檔 ＞ 預設。所以：

- 被環境變數指定的項目，網頁改不了（存了也會被蓋掉）：`describe()` 標成 `source: "env"`，
  `apply()` 拒絕。
- **祕密（token）永遠不離開伺服器**：回應裡只有「有沒有設」與來源，沒有任何字元；
  儲存時沒送的 token 原封不動，送空字串／`null` 才是清掉。錯誤訊息與日誌也不含值。
- 寫檔是原子的（同資料夾寫暫存檔再換名），權限 0600；並帶 `revision`（檔案內容的雜湊），
  別的視窗或人改過就回 409 `stale_config`，不會悄悄蓋掉。
- 設定檔壞了（不是合法 JSON、有不認得的項目）時設定頁仍然打得開，並說明怎麼修；
  覆寫之前一定先備份成 `<檔名>.bak`。
- `VIDPIPE_SETTINGS_READONLY=1`：網頁（與 API）不能改設定，給「對外開放但不想讓人改設定」的部署。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import fields
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import doctor, jobs
from .clients import TTSClient, VidgrabClient
from .config import SPECS, Config, FieldSpec
from .errors import BadInput, ConfigError, FieldErrors, VidpipeError

READONLY_ENV = "VIDPIPE_SETTINGS_READONLY"
TEST_TARGETS = ("vidgrab", "tts", "ffmpeg", "dirs")
SAMPLE_TEXT = "你好，這是一句測試。"

_LOCK = threading.Lock()
_DEFAULTS = Config()


class StaleConfig(jobs.Conflict):
    """編輯時的 revision 已經不是現在的設定檔：不蓋掉對方，請呼叫端重新載入。"""

    code = "stale_config"


class Forbidden(VidpipeError):
    code = "forbidden"


def readonly() -> bool:
    return (os.environ.get(READONLY_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- 讀

def revision_of(file: Path) -> str:
    """設定檔內容的雜湊；沒有檔案是空字串。"""
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _read_tolerant(file: Path) -> tuple[dict[str, Any], dict[str, str] | None]:
    """(內容, 問題)。設定檔壞了不丟例外：設定頁要能打開，才有辦法修。"""
    try:
        return cfgmod.read_file(file), None
    except ConfigError as exc:
        return {}, {"code": "file_invalid", "message": exc.message, "hint": exc.hint}


def _writable(file: Path) -> tuple[bool, str]:
    """寫得進去嗎？檔案不存在就看最近一層存在的資料夾（儲存時會建立中間的資料夾）。"""
    if file.is_dir():
        return False, f"{file} 是個資料夾，不是檔案"
    if file.exists():
        if os.access(file, os.W_OK):
            return True, ""
        return False, f"設定檔 {file} 不可寫"
    ancestor = file.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if ancestor.is_dir() and os.access(ancestor, os.W_OK):
        return True, ""
    return False, f"資料夾 {ancestor} 不可寫"


def _display(spec: FieldSpec, value: Any) -> Any:
    return None if spec.secret else value


def _field(spec: FieldSpec, data: dict[str, Any]) -> dict[str, Any]:
    name = spec.name
    default = getattr(_DEFAULTS, name)
    env_raw = cfgmod.env_value(name)
    in_file = name in data
    source = "env" if env_raw is not None else "file" if in_file else "default"
    raw = env_raw if source == "env" else data[name] if in_file else default
    problem, value = "", raw if isinstance(raw, (str, int, float, bool)) else json.dumps(raw)
    try:
        value = cfgmod.check_field(name, raw)
    except ConfigError as exc:
        problem = exc.message if not exc.hint else f"{exc.message}（{exc.hint}）"
    file_value: Any = None
    if in_file and not spec.secret:
        try:
            file_value = cfgmod.check_field(name, data[name])
        except ConfigError:
            file_value = data[name] if isinstance(data[name], (str, int, float, bool)) else None
    out: dict[str, Any] = {
        "name": name, "group": spec.group, "kind": spec.kind, "label": spec.label,
        "help": spec.help, "placeholder": spec.placeholder, "unit": spec.unit,
        "min": spec.min, "max": spec.max, "step": spec.step, "range_hint": spec.range_hint,
        "choices": [{"value": c.value, "label": c.label, "hint": c.hint} for c in spec.choices],
        "suggestions": list(spec.suggestions), "depends_on": spec.depends_on,
        "required": spec.required, "forbidden": spec.forbidden,
        "forbidden_label": spec.forbidden_label,
        "default": default, "env_var": cfgmod.env_name(name),
        "source": source, "locked": source == "env", "in_file": in_file, "problem": problem,
        # 祕密一個字元都不給：只說有沒有設
        "value": _display(spec, value),
        "file_value": file_value,
        "is_set": bool(value) if spec.secret else None,
    }
    return out


def describe(path: Path | None = None) -> dict[str, Any]:
    """設定頁需要的一切：每個項目的型別、說明、現值、來源，以及設定檔本身的狀態。"""
    file = path or cfgmod.config_path()
    data, file_problem = _read_tolerant(file)
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known)
    problems: list[dict[str, Any]] = []
    if file_problem:
        problems.append(file_problem)
    if unknown:
        problems.append({
            "code": "unknown_keys", "keys": unknown,
            "message": f"設定檔有不認得的項目：{', '.join(unknown)}",
            "hint": "vidpipe 會拒絕載入這樣的設定檔。儲存時這些項目會被移除，舊檔先備份成 .bak"})
    writable, why = _writable(file)
    try:
        cfgmod.load(file)
        load_error = ""
    except ConfigError as exc:
        load_error = exc.message if not exc.hint else f"{exc.message}（{exc.hint}）"
    return {
        "path": str(file.resolve()),
        "path_from_env": bool(os.environ.get("VIDPIPE_CONFIG")),
        "exists": file.exists(),
        "revision": revision_of(file),
        "writable": writable and not readonly(),
        "readonly": readonly(),
        "write_problem": "" if writable else why,
        "problems": problems,
        "load_error": load_error,
        "groups": [{"id": g.id, "title": g.title, "note": g.note} for g in cfgmod.GROUPS],
        "fields": [_field(spec, data) for spec in SPECS.values()],
    }


# ---------------------------------------------------------------- 寫

def _write_file(file: Path, data: dict[str, Any], *, backup: bool) -> None:
    target = file.resolve()          # 設定檔是符號連結（dotfiles）時寫到真正的檔案
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if backup and target.exists():
        bak = target.with_name(target.name + ".bak")
        _atomic_write(bak, target.read_bytes())
    try:
        _atomic_write(target, text.encode("utf-8"))
    except OSError:
        # 單一檔案被 bind mount 進容器時沒辦法換名（EBUSY），資料夾唯讀但檔案可寫也一樣：
        # 檔案本身寫得進去就退回原地覆寫
        if not (target.is_file() and os.access(target, os.W_OK)):
            raise
        with target.open("w", encoding="utf-8") as f:
            f.write(text)


def _atomic_write(target: Path, content: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".vidpipe-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            with contextlib.suppress(AttributeError, OSError):
                os.fchmod(f.fileno(), 0o600)  # 可能放著 token
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _clean_changes(changes: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """把變更套到設定檔內容上；不合格的集中回報（每個欄位一句）。"""
    out = {k: v for k, v in data.items() if k in SPECS}
    errors: dict[str, str] = {}
    for name, raw in changes.items():
        spec = SPECS.get(name)
        if spec is None:
            errors[name] = "不認得的設定項目"
        elif cfgmod.env_value(name) is not None:
            errors[name] = f"由環境變數 {cfgmod.env_name(name)} 指定，網頁改不了"
        elif raw is None:
            out.pop(name, None)             # 還原成預設
        elif spec.secret:
            if not isinstance(raw, str):
                errors[name] = "token 要是文字"
            elif raw.strip():
                out[name] = raw.strip()
            else:
                out.pop(name, None)         # 空字串 = 清掉
        else:
            try:
                out[name] = cfgmod.check_field(name, raw)
            except ConfigError as exc:
                errors[name] = exc.message if not exc.hint else f"{exc.message}（{exc.hint}）"
    if errors:
        n = len(errors)
        raise FieldErrors(f"有 {n} 個設定項目不合格式，沒有儲存任何東西" if n > 1
                          else "設定項目不合格式，沒有儲存", errors)
    return out


def apply(changes: dict[str, Any], *, revision: str | None = None,
          replace_invalid: bool = False, path: Path | None = None) -> dict[str, Any]:
    """儲存。`changes`：{項目: 新值}，`None` = 還原成預設；沒列的項目不動。

    `revision` 給了就必須等於現在設定檔的 revision（不然 409）；`replace_invalid` 允許
    覆寫壞掉的設定檔（先備份）。回傳 `describe()` 加上 `changed`。
    """
    if readonly():
        raise Forbidden("設定頁被設成唯讀",
                        hint=f"部署者設了 {READONLY_ENV}；要改請直接編輯設定檔或環境變數")
    if not isinstance(changes, dict):
        raise BadInput("changes 要是物件：{項目: 新值}")
    file = path or cfgmod.config_path()
    with _LOCK:
        if revision is not None and revision != revision_of(file):
            raise StaleConfig("設定檔在你編輯的時候被改過了",
                              hint="可能是別的視窗、指令列或人改的；重新載入後再改，才不會蓋掉對方")
        data, problem = _read_tolerant(file)
        if problem and not replace_invalid:
            raise jobs.Conflict(
                problem["message"],
                hint="先修好設定檔；或在設定頁選「用預設重建」（舊檔會備份成 .bak）")
        merged = _clean_changes(changes, data)
        dropped = bool(set(data) - set(SPECS)) or bool(problem)
        order = [f.name for f in fields(Config)]
        merged = {k: merged[k] for k in order if k in merged}
        before = {k: v for k, v in data.items() if k in SPECS}
        changed = sorted({k for k in set(merged) | set(before) if merged.get(k) != before.get(k)})
        if changed or dropped or (not file.exists() and merged):
            _write_file(file, merged, backup=dropped)
    return {**describe(file), "changed": changed}


# ---------------------------------------------------------------- 測試連線

def _check(name: str, ok: bool, detail: str = "", hint: str = "", required: bool = True) -> dict:
    return doctor._check(name, ok, detail, hint, required)


def _effective(draft: dict[str, Any], file: Path) -> Config:
    """現在生效的設定，疊上網頁表單還沒儲存的草稿（環境變數指定的項目不受草稿影響）。"""
    data, _ = _read_tolerant(file)
    cfg = Config()
    errors: dict[str, str] = {}
    for spec in SPECS.values():
        name = spec.name
        env = cfgmod.env_value(name)
        if env is not None:
            raw: Any = env
        elif name in draft and not readonly():
            raw = draft[name]
            if raw is None:
                raw = getattr(_DEFAULTS, name)
        else:
            raw = data.get(name, getattr(_DEFAULTS, name))
        try:
            setattr(cfg, name, cfgmod.check_field(name, raw))
        except ConfigError as exc:
            if name in draft and env is None:
                errors[name] = exc.message
            # 現有設定本身不合格（例如環境變數亂填）：測試用預設值，不擋
    if errors:
        raise FieldErrors("表單裡有不合格式的項目，先修正再測試", errors)
    return cfg


def _timed(fn: Any) -> tuple[Any, int]:
    start = time.perf_counter()
    result = fn()
    return result, round((time.perf_counter() - start) * 1000)


def _wav_seconds(data: bytes) -> float | None:
    import io

    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        return None


def test_tts(cfg: Config, *, deep: bool = False) -> dict[str, Any]:
    client = TTSClient(cfg.tts_base_url, cfg.tts_token, model=cfg.tts_model,
                       voice_ref=cfg.voice_ref, reference_text=cfg.reference_text, retries=0)
    models: list[str] = []
    checks = [doctor.check_tts(client, cfg.tts_model, models_out=models),
              doctor.check_voice_ref(cfg)]
    if deep:
        # 真的合成一句（不留檔）：模型與音色參考音檔配不配，只有這樣才知道
        try:
            wav = client.speak(SAMPLE_TEXT)
            secs = _wav_seconds(wav)
            detail = f"合成成功：{secs:.1f} 秒的語音（{len(wav) // 1024} KB）" if secs is not None \
                else f"合成成功（{len(wav) // 1024} KB）"
            checks.append(_check("tts_speak", True, detail))
        except VidpipeError as exc:
            checks.append(_check("tts_speak", False, exc.message, exc.hint))
    return {"checks": checks, "models": models}


def test_vidgrab(cfg: Config) -> dict[str, Any]:
    client = VidgrabClient(cfg.vidgrab_url, cfg.vidgrab_token, retries=0)
    return {"checks": [doctor.check_vidgrab(client)]}


_FONTSELECT = re.compile(
    r"fontselect: \((?P<req>.*), (?P<weight>-?\d+), (?P<italic>-?\d+)\) -> "
    r"(?P<path>.*), (?P<index>-?\d+), (?P<name>.*?)\s*$")
_GLYPH = re.compile(r"Glyph 0x[0-9A-Fa-f]+ not found")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z一-鿿]", "", text.lower())


def _same_font(want: str, name: str, path: str) -> bool:
    w = _norm(want)
    cands = [_norm(name), _norm(Path(path).stem)]
    if any(c and (w in c or c in w) for c in cands):
        return True
    tokens = [_norm(t) for t in want.split() if len(_norm(t)) >= 2]
    return bool(tokens) and any(all(t in c for t in tokens) for c in cands)


_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},24,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,2,0,\
2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,字幕測試 Hello 123
"""


def check_subtitle_font(font: str) -> dict[str, Any]:
    """用 ffmpeg 的 libass 真的畫一格字幕，看它到底選了哪個字體。

    找不到指定字體時 libass 不會報錯，只會悄悄換一個（中文可能變成方塊），所以光看
    「字體清單裡有沒有」不準；這裡讀的是 libass 自己的選字紀錄（`fontselect: …`）。
    """
    name = "subtitle_font"
    if not shutil.which("ffmpeg"):
        return _check(name, False, "沒有 ffmpeg，無法測試", required=False)
    with tempfile.TemporaryDirectory(prefix="vidpipe-font-") as tmp:
        (Path(tmp) / "t.ass").write_text(_ASS.replace("{font}", font), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i",
                 "color=c=black:s=320x180:d=0.2:r=5", "-vf", "ass=t.ass", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30, check=False, cwd=tmp)
        except (OSError, subprocess.SubprocessError) as exc:
            return _check(name, False, f"ffmpeg 跑不起來：{type(exc).__name__}", required=False)
    log = proc.stderr or ""
    if proc.returncode != 0:
        if "No such filter" in log:
            return _check(name, False, "這個 ffmpeg 沒有 libass，畫不了字幕",
                          "換一個含 libass 的 ffmpeg，或把「燒進影片」關掉", required=False)
        last = (log.strip().splitlines() or [""])[-1][:160]
        return _check(name, False, "測試用的字幕畫不出來", last, required=False)
    picks = [m for m in map(_FONTSELECT.search, log.splitlines()) if m]
    mine = [m for m in picks if _norm(m["req"]) == _norm(font)]
    if not mine:
        return _check(name, True, "字幕畫得出來，但看不出字體有沒有被換掉（libass 沒留選字紀錄）",
                      required=False)
    first = mine[0]
    if not _same_font(font, first["name"], first["path"]):
        used = f"{first['name']}（{Path(first['path']).name}）"
        return _check(
            name, False, f"裝不到「{font}」，libass 改用 {used}",
            "安裝這個字體，或改成系統裝得到的繁中字體（例如 Noto Sans CJK TC、PingFang TC）；"
            "不然燒進去的中文可能變方塊", required=False)
    if _GLYPH.search(log):
        extra = ", ".join(sorted({m["name"] for m in mine[1:] if m["name"] != first["name"]}))
        return _check(
            name, False, f"找到「{font}」，但它缺中文字形，缺的字會改用 " + (extra or "別的字體"),
            "換一個有完整繁中字形的字體", required=False)
    return _check(name, True, f"找到「{font}」：{first['name']}（{Path(first['path']).name}）",
                  required=False)


def test_ffmpeg(cfg: Config) -> dict[str, Any]:
    checks = [doctor.check_binary("ffmpeg"), doctor.check_binary("ffprobe"),
              doctor.check_subtitles_filter(cfg), doctor.check_cjk_font()]
    if checks[2]["ok"]:
        checks.append(check_subtitle_font(cfg.subtitle_font))
    return {"checks": checks}


def _dir_check(label: str, value: str) -> dict[str, Any]:
    """不建立任何東西：資料夾還不存在就看最近一層存在的資料夾。"""
    path = Path(value)
    shown = path.resolve()
    anchor = shown
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    name = f"dir_{label}"
    if shown.exists() and not shown.is_dir():
        return _check(name, False, f"{label}：{shown} 不是資料夾", "換一個資料夾路徑")
    if not os.access(anchor, os.W_OK):
        return _check(name, False, f"{label}：{shown} 不可寫（{anchor} 沒有寫入權限）",
                      "換一個可寫的資料夾；Docker 要掛載 volume 並讓 uid 1000 可寫")
    try:
        free = f"，剩 {shutil.disk_usage(anchor).free / 1e9:.1f} GB"
    except OSError:
        free = ""
    state = "可寫" if shown.exists() else "還不存在，第一次用到時會建立"
    return _check(name, True, f"{label}：{shown}（{state}{free}）")


def test_dirs(cfg: Config) -> dict[str, Any]:
    return {"checks": [_dir_check("work", cfg.work_dir), _dir_check("out", cfg.out_dir)]}


def run_test(target: str, draft: dict[str, Any] | None = None, *, deep: bool = False,
             path: Path | None = None) -> dict[str, Any]:
    """測試連線：用「現在生效的設定＋表單裡還沒儲存的草稿」，不寫任何設定。"""
    if target not in TEST_TARGETS:
        raise BadInput(f"不認得的測試項目：{target}", hint=f"可用：{', '.join(TEST_TARGETS)}")
    if draft is not None and not isinstance(draft, dict):
        raise BadInput("values 要是物件：{項目: 值}")
    file = path or cfgmod.config_path()
    cfg = _effective(_draft_values(draft or {}, file), file)
    runners = {"vidgrab": test_vidgrab, "ffmpeg": test_ffmpeg, "dirs": test_dirs,
               "tts": lambda c: test_tts(c, deep=deep)}
    result, ms = _timed(lambda: runners[target](cfg))
    checks = result["checks"]
    return {"target": target, "ok": all(c["ok"] for c in checks if c["required"]),
            "checks": checks, "models": result.get("models", []), "elapsed_ms": ms}


def _draft_values(draft: dict[str, Any], file: Path) -> dict[str, Any]:
    """草稿裡的 token：沒送（或 `None`）就沿用現有的，不是「清掉」。空字串才是清掉。"""
    return {k: v for k, v in draft.items() if k in SPECS and not (SPECS[k].secret and v is None)}
