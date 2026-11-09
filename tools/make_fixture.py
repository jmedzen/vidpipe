#!/usr/bin/env python3
"""造一份假的 work／out 夾，給網頁截圖與手動檢查用。

真的跑 `plan`、`narrate`、`make`，所以檔案長相跟真的一模一樣；只有 TTS 與 vidgrab 是
`tests/` 裡的假服務（合成旁白、合成素材）。不碰你的真資料，也不需要網路。

    python tools/make_fixture.py /tmp/vp-fixture
    VIDPIPE_WORK_DIR=/tmp/vp-fixture/work VIDPIPE_OUT_DIR=/tmp/vp-fixture/out vidpipe serve

造出四支片：做完的、做完但有一個素材授權要確認的、只有旁白的、渲染失敗的。需要 ffmpeg。

加 `--serve` 會在造完之後讓假 TTS 與假 vidgrab 繼續開著，並直接開網頁伺服器：網頁上「新增作品」→
挑素材 → 做成片 → 刪除，整條路都能點，不需要任何真的服務。位址與字體寫在 `<dir>/vidpipe.json`
（不用環境變數），所以「設定」頁改得動、存得下，「測試連線」也是綠的。

    python tools/make_fixture.py /tmp/vp-fixture --serve          # http://127.0.0.1:8320
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from conftest import FakeServer  # noqa: E402
from mediagen import font_name, has_cjk_font, make, tones  # noqa: E402
from ttsfake import FakeTTS  # noqa: E402
from vgfake import FakeVidgrab  # noqa: E402

from vidpipe import cli, planner  # noqa: E402

SCRIPT = ROOT / "examples" / "taipei-night" / "script.txt"
FPS = 10


def run(*argv: str, expect: int = 0) -> dict:
    """跑一個 CLI 指令，回它的 JSON 輸出（stdout 只有 JSON）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    out = json.loads(buf.getvalue())
    if code != expect:
        detail = json.dumps(out, ensure_ascii=False)
        raise SystemExit(f"vidpipe {' '.join(argv)} 回 {code}：{detail}")
    return out


def serve(port: int, root: Path) -> int:
    """網頁伺服器與假服務在同一個行程裡跑；網頁送出的工作是子行程，連的是同一組假服務。"""
    from vidpipe import server

    os.environ["PYTHONPATH"] = str(ROOT / "src")        # 子行程（python -m vidpipe）要找得到它
    httpd = server.make_server("127.0.0.1", port)
    print(json.dumps({"ok": True, "url": f"http://127.0.0.1:{httpd.server_address[1]}",
                      "work_dir": str(root / "work"), "out_dir": str(root / "out"),
                      "note": "Ctrl-C 結束"}, ensure_ascii=False), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def thumb(color: str, label: str, *, wide: bool = False) -> str:
    """假縮圖：data: SVG（CSP 的 img-src 放行 data:，離線也看得到）。"""
    w, h = (320, 180) if wide else (180, 320)
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'>"
           f"<rect width='100%' height='100%' fill='{color}'/>"
           f"<text x='50%' y='50%' fill='white' font-size='28' text-anchor='middle'>{label}</text>"
           "</svg>")
    return "data:image/svg+xml;utf8," + quote(svg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dir", help="要造在哪（會先清掉裡面的 work 與 out）")
    parser.add_argument("--serve", nargs="?", const=8320, type=int, metavar="PORT",
                        help="造完之後開網頁伺服器（預設 8320），假服務不關")
    parser.add_argument("--tts-delay", type=float, default=0.0, metavar="SEC",
                        help="搭配 --serve：每句旁白多等幾秒，才看得到進行中的進度條與取消")
    args = parser.parse_args()
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise SystemExit("需要 ffmpeg 與 ffprobe")
    root = Path(args.dir).resolve()
    for sub in ("work", "out", "src"):
        shutil.rmtree(root / sub, ignore_errors=True)
    (root / "src").mkdir(parents=True)

    tts, vg_server = FakeServer(), FakeServer()
    fake_tts = FakeTTS(tts, speech=lambda text: 0.2 * len(text))
    footage = make(root / "src" / "footage.mp4", "color=c=teal:s=540x960", audio=True,
                   seconds=10, fps=25).read_bytes()
    song = tones(root / "src" / "song.wav", [(220.0, 40.0)], amp=0.25).read_bytes()
    vg = FakeVidgrab(vg_server, footage)
    vg.file_bytes = lambda path: song if "jamendo" in path else footage

    def search(body: dict) -> list[dict]:
        cand = FakeVidgrab.candidate
        if body["platform"] == "music":
            found = [cand("jamendo:1", duration=40, kind="free-attribution", title="Calm piano",
                          width=0, height=0, thumbnail=thumb("#6a4a7a", "♪", wide=True)),
                     cand("jamendo:2", duration=90, kind="free", title="Soft rain",
                          width=0, height=0, thumbnail=thumb("#3a5a7a", "♪", wide=True))]
        else:
            n = len(vg.searches)
            kind = "check" if "risky" in " ".join(body["keywords"]) else "free"
            found = [cand(f"pixabay:{n}1", duration=10, kind=kind, title=f"夜市畫面 {n}",
                          thumbnail=thumb("#2d6a6a", "9:16")),
                     cand(f"pixabay:{n}2", duration=10, width=1920, height=1080, kind=kind,
                          title=f"街景（橫式）{n}", thumbnail=thumb("#8a5a2b", "16:9", wide=True)),
                     cand(f"pixabay:{n}3", duration=3, kind=kind, title=f"短短的一段 {n}",
                          thumbnail=thumb("#5b4b8a", "3s"))]
        for item in found:
            vg.items[item["id"]] = item
        return found

    vg.search_handler = search
    # 位址與字體寫進設定檔、不用環境變數：網頁的「設定」頁才改得動、存得下，測試連線也是綠的
    for name in [n for n in os.environ if n.startswith("VIDPIPE_")]:
        del os.environ[name]
    settings = {"tts_base_url": tts.url, "vidgrab_url": vg_server.url,
                "work_dir": str(root / "work"), "out_dir": str(root / "out")}
    if has_cjk_font():
        settings["subtitle_font"] = font_name()
    config_file = root / "vidpipe.json"
    config_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.environ["VIDPIPE_CONFIG"] = str(config_file)
    burn = has_cjk_font()

    paragraphs = SCRIPT.read_text(encoding="utf-8").strip().split("\n\n")

    def draft(short_id: str, title: str, take: int, **extra: object) -> Path:
        doc = planner.parse_script("\n\n".join(paragraphs[:take]), short_id=short_id,
                                   title=title)
        doc.update({"output": {"fps": FPS}, "subtitles": {"burn_in": burn}, **extra})
        path = root / "src" / f"{short_id}-draft.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return path

    # 1. 做完的：plan → make，有音樂
    ok = root / "src" / "taipei-night-01.json"
    run("plan", str(draft("taipei-night-01", "台北夜市", 4)), "--music", "calm piano",
        "-o", str(ok))
    run("make", str(ok))

    # 2. 做完但有一個素材授權要確認：plan 放行不明授權，render 會把它標出來
    risky = draft("old-bookstore-02", "老書店的夜", 3)
    doc = json.loads(risky.read_text(encoding="utf-8"))
    doc["scenes"][1]["query"] = ["risky archive footage"]
    risky.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    planned = root / "src" / "old-bookstore-02.json"
    run("plan", str(risky), "--allow-unknown-license", "-o", str(planned))
    run("make", str(planned))

    # 3. 只有旁白與時間軸
    only = root / "src" / "late-walk-03.json"
    run("plan", str(draft("late-walk-03", "夜歸", 2)), "-o", str(only))
    run("narrate", str(only))

    # 4. 渲染失敗：素材檔不存在
    broken = json.loads(only.read_text(encoding="utf-8"))
    broken.update({"id": "broken-04", "title": "素材不見了"})
    broken["scenes"][0]["clip"] = {"path": str(root / "src" / "missing.mp4")}
    path = root / "src" / "broken-04.json"
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    run("narrate", str(path))
    run("render", str(path), expect=1)

    if args.serve:
        fake_tts.delay = args.tts_delay       # 造片時不延遲，只讓之後從網頁送的工作變慢
        return serve(args.serve, root)
    tts.close()
    vg_server.close()
    config_file.unlink()                      # 假服務關了，裡面的位址已經是死的
    print(json.dumps({"ok": True, "work_dir": str(root / "work"), "out_dir": str(root / "out"),
                      "shorts": ["taipei-night-01", "old-bookstore-02", "late-walk-03",
                                 "broken-04"],
                      "serve": f"VIDPIPE_WORK_DIR={root / 'work'} VIDPIPE_OUT_DIR={root / 'out'}"
                               " vidpipe serve --no-open"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
