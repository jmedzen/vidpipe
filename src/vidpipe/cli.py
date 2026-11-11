"""命令列。stdout 只有 JSON（給 agent），人看的訊息走 stderr。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from . import config as config_mod
from .errors import BadInput, VidpipeError, from_exception
from .version import __version__, build, full_version


def emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_version(args: argparse.Namespace) -> int:
    emit({"ok": True, "version": __version__, "build": build(), "full": full_version()})
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = config_mod.load()
    emit({"ok": True, "path": str(config_mod.config_path()), "config": cfg.to_dict(mask=True)})
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor

    skip = [x.strip() for x in (args.skip or "").split(",") if x.strip()]
    result = doctor.run(config_mod.load(), skip=skip)
    emit(result)
    return 0 if result["ok"] else 1


def cmd_probe(args: argparse.Namespace) -> int:
    from . import ffmpeg

    emit({"ok": True, **ffmpeg.probe(args.path)})
    return 0


_RESULT_KEYS = ("path", "duration", "width", "height", "fps", "looped", "size_bytes")


def _recorded(kind: str, params: dict[str, Any], run: Callable[[], dict[str, Any]],
              summary: Callable[[dict[str, Any]], dict[str, Any]] | None = None, *,
              short_id: str | None = None) -> int:
    """跑一個長指令並留下工作紀錄（網頁看得到進度）；輸出多一個 `job`（沒記錄就是 null）。

    `summary` 把輸出縮成存進紀錄的「結果」；預設只留檔案資訊那幾個欄位。
    `short_id` 標明這個工作屬於哪支片（manifest 的 id）。
    """
    from . import jobs

    cfg = config_mod.load()
    with jobs.JobRecorder(cfg.work_dir, kind, args=params, short_id=short_id,
                          enabled=cfg.record_jobs) as job:
        out = run()
        job.set_result(summary(out) if summary else {k: out[k] for k in _RESULT_KEYS if k in out})
    emit({"ok": True, "job": job.id if job.enabled else None, "output": out})
    return 0


def _read_script(args: argparse.Namespace) -> str:
    if args.text is not None:
        return str(args.text)
    if not args.script:
        raise BadInput("要給腳本檔或 --text",
                       hint="例：vidpipe split script.txt，或 --text \"一句話。\"")
    if args.script == "-":
        return sys.stdin.read()
    from pathlib import Path

    path = Path(args.script)
    if not path.is_file():
        from .errors import NotFound

        raise NotFound(f"找不到腳本檔：{path}")
    return path.read_text(encoding="utf-8")


def cmd_split(args: argparse.Namespace) -> int:
    from . import text

    sentences = text.split_sentences(_read_script(args), args.max_chars)
    emit({"ok": True, "count": len(sentences),
          "sentences": [{"index": i, "text": t, "chars": len(t)} for i, t in enumerate(sentences)]})
    return 0


def cmd_speak(args: argparse.Namespace) -> int:
    from . import narration, text

    sentences = text.split_sentences(_read_script(args), args.max_chars)
    cfg = config_mod.load()

    def run() -> dict[str, Any]:
        clips = narration.synthesize(sentences, cfg)
        if args.out_dir:
            import shutil
            from pathlib import Path

            dest = Path(args.out_dir)
            dest.mkdir(parents=True, exist_ok=True)
            width = max(3, len(str(len(clips))))
            for clip in clips:
                shutil.copyfile(clip.path, dest / f"{clip.index + 1:0{width}d}.wav")
        return {"count": len(clips), "cached": sum(c.cached for c in clips),
                "total_duration": round(sum(c.duration for c in clips), 3),
                "clips": [c.to_dict() for c in clips]}

    return _recorded("narrate", {"sentences": len(sentences), "max_chars": args.max_chars,
                                 "model": cfg.tts_model or None}, run,
                     summary=lambda o: {k: o[k] for k in ("count", "cached", "total_duration")})


def _load_manifest(path: str) -> tuple[Any, Any, Any]:
    """讀＋驗證 manifest，回傳 (原稿, Manifest, 套用 manifest.voice 後的設定)。"""
    from . import manifest as manifest_mod

    cfg = config_mod.load()
    raw = manifest_mod.read_json(path)
    manifest = manifest_mod.parse(raw, subtitle_langs=cfg.subtitle_langs,
                                  burn_in=cfg.burn_subtitles)
    return raw, manifest, manifest_mod.apply_voice(manifest, cfg)


def cmd_validate(args: argparse.Namespace) -> int:
    """只檢查 manifest（不呼叫 TTS、不碰素材）；回每個場景的分句與粗估長度。"""
    from . import shorts

    _, manifest, _ = _load_manifest(args.manifest)
    emit({"ok": True, **shorts.describe(manifest)})
    return 0


def cmd_narrate(args: argparse.Namespace) -> int:
    from . import timeline

    raw, manifest, cfg = _load_manifest(args.manifest)
    return _recorded(
        "narrate", {"manifest": args.manifest, "scenes": len(manifest.scenes),
                    "model": cfg.tts_model or None},
        lambda: timeline.run(manifest, raw, cfg),
        summary=lambda o: {k: o[k] for k in ("short_id", "total", "warnings")},
        short_id=manifest.id)


def cmd_render(args: argparse.Namespace) -> int:
    from . import render

    raw, manifest, cfg = _load_manifest(args.manifest)
    return _recorded(
        "render", {"manifest": args.manifest, "scenes": len(manifest.scenes),
                   "output": args.output},
        lambda: render.run(manifest, raw, cfg, args.output),
        summary=lambda o: {k: o[k] for k in ("short_id", "path", "duration", "size_bytes",
                                             "warnings")},
        short_id=manifest.id)


def cmd_make(args: argparse.Namespace) -> int:
    from . import shorts

    raw, manifest, cfg = _load_manifest(args.manifest)
    return _recorded(
        "make", {"manifest": args.manifest, "scenes": len(manifest.scenes),
                 "output": args.output, "model": cfg.tts_model or None},
        lambda: shorts.make(manifest, raw, cfg, args.output),
        summary=lambda o: {k: o[k] for k in ("short_id", "path", "duration", "size_bytes",
                                             "warnings")},
        short_id=manifest.id)


def _plan_input(args: argparse.Namespace) -> Any:
    """`.json`（或內容以 `{` 開頭）是草稿 manifest；其他當純文字腳本。"""
    from . import planner

    text = _read_script(args)
    if str(args.script).endswith(".json") or text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise BadInput(f"草稿不是合法的 JSON：{exc}") from exc
    if not args.id:
        raise BadInput("純文字腳本要用 --id 指定作品 id",
                       hint="例：vidpipe plan script.txt --id taipei-night-01")
    return planner.parse_script(text, short_id=args.id, title=args.title or "")


def cmd_plan(args: argparse.Namespace) -> int:
    """草稿 → 去 vidgrab 搜尋、挑素材與音樂 → 可以直接 make 的 manifest（不呼叫 TTS、不下載）。"""
    from . import planner

    cfg = config_mod.load()
    doc = planner.apply_options(_plan_input(args), music=args.music,
                                allow_unknown_license=args.allow_unknown_license,
                                platform=args.platform, orientation=args.orientation)
    req = planner.parse_request(doc, cfg)          # 先驗證：寫錯就不開工作

    def run() -> dict[str, Any]:
        result = planner.run(req, cfg)
        planner.store_result(cfg, req.manifest.id, result)
        if args.out:
            result["path"] = str(planner.write_manifest(args.out, result["manifest"]))
        return result

    return _recorded("plan", {"id": req.manifest.id, "scenes": len(req.manifest.scenes)}, run,
                     summary=planner.summarize, short_id=req.manifest.id)


def cmd_quick(args: argparse.Namespace) -> int:
    """文案 → 成片，不需要 LLM。單筆（.txt 或一個 JSON 物件）或批次（JSON 清單、JSONL、CSV）。"""
    from . import quick

    cfg = config_mod.load()
    text = args.text if args.text else _read_script(args)
    name = "" if args.text else str(args.script or "")
    options = {k: v for k, v in (("id", args.id), ("title", args.title),
                                 ("series", args.series)) if v}
    if args.no_music:
        options["music"] = False
    if args.only_clear_license:
        options["allow_unknown_license"] = False
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    head = text.lstrip("\ufeff").lstrip()[:1]
    if suffix in ("csv", "jsonl") or head == "[" or (suffix == "json" and head != "{"):
        reqs = [{**r, **{k: v for k, v in options.items() if k != "id"}}
                for r in quick.parse_batch(text, fmt=suffix if suffix != "json" else "json")]
        return _recorded("quick-batch", {"items": len(reqs), "ids": [r["id"] for r in reqs]},
                         lambda: quick.run_batch(reqs, cfg),
                         summary=lambda o: {k: o[k] for k in ("total", "done", "failed")})
    doc = json.loads(text) if head == "{" else {"text": text}
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        reqs = quick.parse_batch(doc)
        return _recorded("quick-batch", {"items": len(reqs), "ids": [r["id"] for r in reqs]},
                         lambda: quick.run_batch(reqs, cfg),
                         summary=lambda o: {k: o[k] for k in ("total", "done", "failed")})
    req = quick.parse_request({**doc, **options})
    if args.dry_run:
        emit({"ok": True, **quick.build(req, cfg)})
        return 0
    return _recorded(
        "quick", {"id": req["id"], "title": req["title"], "chars": quick.chars(req["text"])},
        lambda: quick.run(req, cfg, output=args.output),
        summary=lambda o: {k: o[k] for k in ("short_id", "path", "duration", "size_bytes",
                                             "warnings")},
        short_id=req["id"])


def cmd_voices(args: argparse.Namespace) -> int:
    """參考音檔資料夾：列出音檔與逐字稿；--transcribe 用 audio.cpp 的 ASR 補上沒有的。"""
    from . import voices

    cfg = config_mod.load()
    if args.dir:
        from dataclasses import replace

        cfg = replace(cfg, voice_ref_dir=args.dir)
    if args.model:
        from dataclasses import replace

        cfg = replace(cfg, asr_model=args.model)
    if not args.transcribe:
        emit({"ok": True, "dir": cfg.voice_ref_dir, "items": voices.scan(cfg)})
        return 0
    return _recorded("voices", {"dir": cfg.voice_ref_dir, "model": cfg.asr_model},
                     lambda: voices.transcribe_missing(cfg),
                     summary=lambda o: {k: o[k] for k in ("dir", "existing", "transcribed",
                                                          "failed")})


def cmd_credits(args: argparse.Namespace) -> int:
    from . import render

    raw, manifest, cfg = _load_manifest(args.manifest)
    return _recorded("credits", {"manifest": args.manifest, "output": args.output},
                     lambda: render.write_credits(manifest, raw, cfg, args.output),
                     summary=lambda o: {k: o[k] for k in ("short_id", "path", "assets",
                                                          "attribution_required", "unclear")},
                     short_id=manifest.id)


def cmd_cover(args: argparse.Namespace) -> int:
    from . import render

    raw, manifest, cfg = _load_manifest(args.manifest)
    return _recorded("cover", {"manifest": args.manifest, "output": args.output,
                               "at": args.at, "text": args.text},
                     lambda: render.write_cover(manifest, raw, cfg, args.output, at=args.at,
                                                text=args.text),
                     summary=lambda o: {k: o[k] for k in ("short_id", "path", "at", "text")},
                     short_id=manifest.id)


def cmd_normalize(args: argparse.Namespace) -> int:
    from . import video

    spec = video.OutputSpec.for_aspect(args.aspect, fps=args.fps)
    params = {"input": args.input, "output": args.output, "aspect": args.aspect,
              "fit": args.fit, "focus": args.focus, "fps": args.fps, "audio": args.audio}
    return _recorded("normalize", params, lambda: video.normalize(
        args.input, args.output, spec, fit=args.fit, focus=args.focus, audio=args.audio))


def cmd_trim(args: argparse.Namespace) -> int:
    from . import edit

    params = {"input": args.input, "output": args.output, "start": args.start,
              "duration": args.duration}
    return _recorded("trim", params, lambda: edit.trim(
        args.input, args.output, args.start, args.duration))


def cmd_fill(args: argparse.Namespace) -> int:
    from . import edit

    params = {"input": args.input, "output": args.output, "start": args.start,
              "duration": args.duration, "loop": not args.no_loop}
    return _recorded("fill", params, lambda: edit.fill(
        args.input, args.output, args.duration, start=args.start, loop=not args.no_loop))


def cmd_concat(args: argparse.Namespace) -> int:
    from . import edit

    params = {"inputs": list(args.inputs), "output": args.output, "copy": args.copy}
    return _recorded("concat", params, lambda: edit.concat(
        args.inputs, args.output, copy=args.copy))


def cmd_jobs(args: argparse.Namespace) -> int:
    from . import jobs

    cfg = config_mod.load()
    emit({"ok": True, "jobs": jobs.list_jobs(cfg.work_dir, limit=args.limit, status=args.status)})
    return 0


def cmd_job(args: argparse.Namespace) -> int:
    from . import jobs

    cfg = config_mod.load()
    job = jobs.get_job(cfg.work_dir, args.id)
    emit({"ok": True, "job": job, "events": jobs.read_events(cfg.work_dir, args.id, args.after)})
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    from . import jobs

    cfg = config_mod.load()
    emit({"ok": True, "job": jobs.request_cancel(cfg.work_dir, args.id)})
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from . import server

    try:
        return server.serve(args.host, args.port, open_browser=not args.no_open,
                            allow_lan=args.allow_lan, no_auth=args.no_auth)
    except OSError as exc:  # port 被占用之類
        raise VidpipeError(f"伺服器起不來：{exc}",
                           hint="換一個 --port，或確認沒有重複啟動") from exc


def cmd_mcp(args: argparse.Namespace) -> int:
    from . import mcp

    return mcp.serve_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vidpipe",
        description="Shorts 流水線：腳本 → 旁白與字幕 → 素材 → 9:16 成品",
    )
    parser.add_argument("--version", action="version", version=f"vidpipe {full_version()}")
    parser.add_argument("--debug", action="store_true", help="出錯時印出完整追蹤")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="版本與 build number（JSON）").set_defaults(func=cmd_version)
    sub.add_parser("config", help="目前生效的設定（祕密遮蔽）").set_defaults(func=cmd_config)
    p = sub.add_parser("probe", help="影片／音訊檔的基本資訊（JSON）")
    p.add_argument("path")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("normalize", help="轉成統一規格並重構圖（9:16 等）")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--aspect", default="9:16", choices=["9:16", "1:1", "16:9"])
    p.add_argument("--fit", default="blur_pad", choices=["crop", "blur_pad", "letterbox"],
                   help="長寬比對不上時怎麼處理：裁切／模糊底／黑邊")
    p.add_argument("--focus", help="crop 時裁哪一塊，x,y（0–1），預設置中 0.5,0.5")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--audio", default="keep", choices=["keep", "silent"],
                   help="keep 保留原音（沒有就補靜音軌）；silent 一律靜音軌")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("trim", help="精準裁切（重編碼，不會差到關鍵幀）")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--start", type=float, default=0.0, help="起點秒數")
    p.add_argument("--duration", type=float, help="長度秒數，省略 = 到尾")
    p.set_defaults(func=cmd_trim)

    p = sub.add_parser("fill", help="湊成指定長度：夠長就裁，不夠就循環")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--duration", type=float, required=True, help="目標長度秒數")
    p.add_argument("--start", type=float, default=0.0, help="起點秒數")
    p.add_argument("--no-loop", action="store_true", help="不夠長時報錯而不是循環")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("concat", help="串接多支同規格的片段（先 normalize）")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--copy", action="store_true",
                   help="不重編碼（快、零損失；AAC 接縫可能有幾十毫秒誤差）")
    p.set_defaults(func=cmd_concat)

    p = sub.add_parser("split", help="旁白文字分句（每句各自合成，也就是字幕的節奏）")
    p.add_argument("script", nargs="?", help="腳本檔；- 讀 stdin")
    p.add_argument("--text", help="直接給文字")
    p.add_argument("--max-chars", type=int, default=40,
                   help="一句最多幾字，超過在逗號處切（預設 40）")
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("speak", help="分句後逐句合成旁白（有快取），輸出每句的檔案與秒數")
    p.add_argument("script", nargs="?", help="腳本檔；- 讀 stdin")
    p.add_argument("--text", help="直接給文字")
    p.add_argument("--max-chars", type=int, default=40)
    p.add_argument("--out-dir", help="另外把每句複製成 001.wav、002.wav… 到這個資料夾")
    p.set_defaults(func=cmd_speak)

    p = sub.add_parser("validate", help="檢查 manifest（不呼叫 TTS），回分句與粗估長度")
    p.add_argument("manifest", help="manifest JSON 檔")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("narrate", help="照 manifest 合成旁白並排時間軸（旁白音軌、場景長度）")
    p.add_argument("manifest", help="manifest JSON 檔")
    p.set_defaults(func=cmd_narrate)

    p = sub.add_parser("render", help="照 manifest 與 narrate 的結果渲染成片（mp4）")
    p.add_argument("manifest", help="manifest JSON 檔（要先跑過 narrate）")
    p.add_argument("-o", "--output", help="輸出檔，預設 <out_dir>/<id>.mp4")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("make", help="一次做完：narrate + render（含封面與 credits）")
    p.add_argument("manifest", help="manifest JSON 檔")
    p.add_argument("-o", "--output", help="輸出檔，預設 <out_dir>/<id>.mp4")
    p.set_defaults(func=cmd_make)

    p = sub.add_parser("voices",
                       help="參考音檔資料夾：列出音檔與逐字稿；--transcribe 用 ASR 補上沒有的")
    p.add_argument("--dir", help="資料夾（預設用設定的 voice_ref_dir）")
    p.add_argument("--model", help="ASR 模型 id（預設用設定的 asr_model）")
    p.add_argument("--transcribe", action="store_true",
                   help="沒有逐字稿（同名 .txt）的用 audio.cpp 的 ASR 辨識，存回同一個資料夾")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("quick", help="文案直出：一段文案（或一批 CSV／JSON）→ 成片，不需要 LLM")
    p.add_argument("script", nargs="?",
                   help="文案 .txt、單筆 .json、批次 .json／.jsonl／.csv；- 讀 stdin")
    p.add_argument("--text", help="直接給文案（不讀檔）")
    p.add_argument("--id", help="作品 id（單筆；不給就用日期加文案雜湊）")
    p.add_argument("--title", help="畫面上方的常駐標題（不給就用第一句）")
    p.add_argument("--series", help="畫面下方的系列名稱")
    p.add_argument("--no-music", action="store_true", help="不放背景音樂")
    p.add_argument("--only-clear-license", action="store_true",
                   help="只用授權明確的素材（預設也放行授權未標示的，credits 會標出來）")
    p.add_argument("--dry-run", action="store_true", help="只分鏡與挑素材，回 manifest，不做成片")
    p.add_argument("-o", "--output", help="輸出檔（單筆），預設 <out_dir>/<id>.mp4")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("plan", help="草稿（場景可以只寫 query）→ 搜尋並挑素材與音樂 → manifest")
    p.add_argument("script", help="草稿 .json，或純文字腳本（空白行隔開場景）；- 讀 stdin")
    p.add_argument("--id", help="作品 id（純文字腳本必填）")
    p.add_argument("--title", help="標題（純文字腳本用）")
    p.add_argument("-o", "--out", help="把挑好的 manifest 另存成這個檔案")
    p.add_argument("--music", help="背景音樂的搜尋關鍵字（會蓋過草稿的 music.query）")
    p.add_argument("--platform", help="stock（預設）、pexels、pixabay、bilibili、youtube、both")
    p.add_argument("--orientation", help="portrait（預設）、landscape、square、any")
    p.add_argument("--allow-unknown-license", action="store_true",
                   help="授權不明或要確認的素材也考慮（會留警告）")
    p.set_defaults(func=cmd_plan, text=None)

    p = sub.add_parser("cover", help="用最近一次渲染的畫面重做封面（改 at 或標題不用重新渲染）")
    p.add_argument("manifest", help="manifest JSON 檔（要先跑過 render）")
    p.add_argument("--at", type=float, help="取第幾秒的畫面；預設用 manifest 的 cover.at 或 1 秒")
    p.add_argument("--text", help="封面標題；空字串 = 不放字；預設用 cover.text，再沒有就用 title")
    p.add_argument("-o", "--output", help="輸出檔，預設 <out_dir>/<id>.cover.jpg")
    p.set_defaults(func=cmd_cover)

    p = sub.add_parser("credits", help="重做素材來源與授權清單（credits.md）")
    p.add_argument("manifest", help="manifest JSON 檔（要先跑過 render）")
    p.add_argument("-o", "--output", help="輸出檔，預設 <out_dir>/<id>.credits.md")
    p.set_defaults(func=cmd_credits)

    p = sub.add_parser("jobs", help="工作紀錄清單（新的在前）")
    p.add_argument("--status", help="running／succeeded／failed／cancelled／interrupted")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("job", help="單一工作的狀態與事件")
    p.add_argument("id")
    p.add_argument("--after", type=int, default=0, help="只要 seq 大於這個值的事件")
    p.set_defaults(func=cmd_job)

    p = sub.add_parser("cancel", help="取消還在跑的工作（同一台機器上）")
    p.add_argument("id")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("serve", help="開網頁介面與 REST API（狀態、進度、預覽）")
    p.add_argument("--host", help="綁定位址，預設 127.0.0.1（或 VIDPIPE_HOST）")
    p.add_argument("--port", type=int, help="預設 8320（或 VIDPIPE_PORT）")
    p.add_argument("--no-open", action="store_true", help="不自動開瀏覽器")
    p.add_argument("--allow-lan", action="store_true",
                   help="允許綁非本機位址（Docker 裡必須；同時要設 VIDPIPE_API_TOKEN）")
    p.add_argument("--no-auth", action="store_true",
                   help="綁非本機位址也不要 token（外層已有保護時才用）")
    p.set_defaults(func=cmd_serve)

    sub.add_parser("mcp", help="MCP server（stdio）：給 Claude Code 等 client 以子行程啟動"
                   ).set_defaults(func=cmd_mcp)

    p = sub.add_parser("doctor", help="檢查 ffmpeg、vidgrab、TTS 是否都就緒（JSON）")
    p.add_argument("--skip", help="略過的項目，逗號分隔（例如 vidgrab,tts）")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BaseException as exc:  # noqa: BLE001 - 一律轉成結構化錯誤
        if args.debug:
            import traceback

            traceback.print_exc()
        err: VidpipeError = from_exception(exc)
        emit(err.to_dict())
        return 130 if isinstance(exc, KeyboardInterrupt) else 2 if isinstance(err, BadInput) else 1


if __name__ == "__main__":
    sys.exit(main())
