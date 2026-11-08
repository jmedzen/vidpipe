"""命令列。stdout 只有 JSON（給 agent），人看的訊息走 stderr。"""

from __future__ import annotations

import argparse
import json
import sys
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


def cmd_normalize(args: argparse.Namespace) -> int:
    from . import video

    spec = video.OutputSpec.for_aspect(args.aspect, fps=args.fps)
    out = video.normalize(args.input, args.output, spec, fit=args.fit, focus=args.focus,
                          audio=args.audio)
    emit({"ok": True, "output": out})
    return 0


def cmd_trim(args: argparse.Namespace) -> int:
    from . import edit

    out = edit.trim(args.input, args.output, args.start, args.duration)
    emit({"ok": True, "output": out})
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    from . import edit

    out = edit.fill(args.input, args.output, args.duration, start=args.start,
                    loop=not args.no_loop)
    emit({"ok": True, "output": out})
    return 0


def cmd_concat(args: argparse.Namespace) -> int:
    from . import edit

    out = edit.concat(args.inputs, args.output, copy=args.copy)
    emit({"ok": True, "output": out})
    return 0


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
