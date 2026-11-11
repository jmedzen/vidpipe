"""入口:./pipeline run|batch|status|qc"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from vidpipe.errors import VidpipeError

from . import config, llm, stages, vidpipe

EXIT_NEEDS_LLM = 3
# 批次的長度分布:約四成 30–60 秒、四成 60–90 秒、兩成 90–120 秒
BATCH_SECONDS = [45, 75, 105, 50, 80, 40, 70, 110, 55, 85]


def _find_topic(cfg: dict, topic_id: str) -> dict:
    for t in stages.load_topics(cfg):
        if t["id"] == topic_id or t["name"] == topic_id:
            return t
    raise SystemExit(f"主題庫沒有「{topic_id}」。可用:" +
                     "、".join(t["id"] for t in stages.load_topics(cfg)))


def _one(cfg: dict, topic: dict, fmt: str, seconds: int, date: str | None) -> tuple[str, str]:
    """跑一支。回 (狀態, 說明):done / waiting / rejected / failed。"""
    run = stages.Run(cfg, topic, fmt, seconds, date)
    try:
        dest = run.run()
    except llm.NeedsResponse as exc:
        run.log(str(exc))
        return "waiting", f"{exc.prompt} → {exc.response}"
    except stages.Rejected as exc:
        run.log(f"退回:{exc}")
        return "rejected", str(exc)
    except (vidpipe.VidpipeError, VidpipeError, llm.LLMError) as exc:
        run.log(f"失敗:{exc}" + (f"({exc.hint})" if getattr(exc, "hint", "") else ""))
        return "failed", str(exc)
    stages.mark_done(cfg, run, dest, stages.read(dest / "metadata.json")["duration"])
    return "done", str(dest)


def cmd_run(args, cfg: dict) -> int:
    seconds = args.seconds or (480 if args.format == "long" else 60)
    status, detail = _one(cfg, _find_topic(cfg, args.topic), args.format, seconds, args.date)
    print(json.dumps({"status": status, "detail": detail}, ensure_ascii=False))
    return {"done": 0, "waiting": EXIT_NEEDS_LLM}.get(status, 1)


def cmd_batch(args, cfg: dict) -> int:
    done_path = stages.done_file(cfg)
    done = {d["topic"] for d in (stages.read(done_path) if done_path.is_file() else [])
            if d["format"] == args.format}
    plan_file = Path(cfg["work"]) / f"batch_{args.name}.json"
    if plan_file.is_file():       # 續跑:沿用同一份計畫
        plan = stages.read(plan_file)
    else:
        topics = [t for t in stages.load_topics(cfg) if t["id"] not in done][: args.count]
        plan = [{"topic": t["id"], "format": args.format,
                 "seconds": 480 if args.format == "long" else BATCH_SECONDS[i % len(BATCH_SECONDS)],
                 # 日期寫進計畫:隔天續跑同一批,還是接著同一組 run,不會另開新的
                 "date": args.date or dt.date.today().strftime("%Y%m%d")}
                for i, t in enumerate(topics)]
        stages.write(plan_file, plan)
    results = []
    for item in plan:     # 一支失敗不影響其他支;重跑同一個指令只補沒做完的
        status, detail = _one(cfg, _find_topic(cfg, item["topic"]), item["format"],
                              item["seconds"], item.get("date"))
        results.append({**item, "status": status, "detail": detail})
    print(json.dumps({"batch": args.name, "results": results}, ensure_ascii=False, indent=1))
    if all(r["status"] == "done" for r in results):
        return 0
    return EXIT_NEEDS_LLM if any(r["status"] == "waiting" for r in results) else 1


def cmd_status(args, cfg: dict) -> int:
    rows = []
    for d in sorted(Path(cfg["work"]).glob("2*_*_*")):
        if not d.is_dir():
            continue
        stage = "new"
        for name, label in (("brief.json", "brief"), ("script.json", "script"),
                            ("footage.json", "visuals"), ("make.json", "made"),
                            ("package.json", "packaged"), ("qc.json", "qc")):
            if (d / name).is_file():
                stage = label
        waiting = [p.name for p in d.glob("*.prompt.md")
                   if not (d / p.name.replace(".prompt.md", ".response.json")).is_file()]
        rows.append({"run": d.name, "stage": stage, "waiting_for": waiting})
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline", description="佛法短影音流水線")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="一個主題做成一支片")
    r.add_argument("--topic", required=True)
    r.add_argument("--format", choices=["short", "long"], default="short")
    r.add_argument("--seconds", type=int, default=0)
    r.add_argument("--date", default=None, help="YYYYMMDD,續跑隔天的工作時指定")
    b = sub.add_parser("batch", help="批次產出;重跑同一個 --name 會續跑")
    b.add_argument("--count", type=int, default=5)
    b.add_argument("--format", choices=["short", "long"], default="short")
    b.add_argument("--name", default="default")
    b.add_argument("--date", default=None)
    sub.add_parser("status", help="每個 run 做到哪")
    args = p.parse_args(argv)
    cfg = config.load()
    return {"run": cmd_run, "batch": cmd_batch, "status": cmd_status}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
