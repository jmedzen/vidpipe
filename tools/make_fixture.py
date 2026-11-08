#!/usr/bin/env python3
"""造一份假的 work／out 夾，給網頁截圖與手動檢查用。

真的跑 `plan`、`narrate`、`make`，所以檔案長相跟真的一模一樣；只有 TTS 與 vidgrab 是
`tests/` 裡的假服務（合成旁白、合成素材）。不碰你的真資料，也不需要網路。

    python tools/make_fixture.py /tmp/vp-fixture
    VIDPIPE_WORK_DIR=/tmp/vp-fixture/work VIDPIPE_OUT_DIR=/tmp/vp-fixture/out vidpipe serve

造出四支片：做完的、做完但有一個素材授權要確認的、只有旁白的、渲染失敗的。需要 ffmpeg。
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dir", help="要造在哪（會先清掉裡面的 work 與 out）")
    args = parser.parse_args()
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise SystemExit("需要 ffmpeg 與 ffprobe")
    root = Path(args.dir).resolve()
    for sub in ("work", "out", "src"):
        shutil.rmtree(root / sub, ignore_errors=True)
    (root / "src").mkdir(parents=True)

    tts, vg_server = FakeServer(), FakeServer()
    FakeTTS(tts, speech=lambda text: 0.2 * len(text))
    footage = make(root / "src" / "footage.mp4", "color=c=teal:s=540x960", audio=True,
                   seconds=10, fps=25).read_bytes()
    song = tones(root / "src" / "song.wav", [(220.0, 40.0)], amp=0.25).read_bytes()
    vg = FakeVidgrab(vg_server, footage)
    vg.file_bytes = lambda path: song if "jamendo" in path else footage

    def search(body: dict) -> list[dict]:
        cand = FakeVidgrab.candidate
        if body["platform"] == "music":
            found = [cand("jamendo:1", duration=40, kind="free-attribution",
                          title="Calm piano")]
        else:
            n = len(vg.searches)
            kind = "check" if "risky" in " ".join(body["keywords"]) else "free"
            found = [cand(f"pixabay:{n}1", duration=10, kind=kind, title=f"夜市畫面 {n}"),
                     cand(f"pixabay:{n}2", duration=10, width=1920, height=1080, kind=kind,
                          title=f"街景（橫式）{n}")]
        for item in found:
            vg.items[item["id"]] = item
        return found

    vg.search_handler = search
    os.environ.update({
        "VIDPIPE_TTS_BASE_URL": tts.url, "VIDPIPE_VIDGRAB_URL": vg_server.url,
        "VIDPIPE_WORK_DIR": str(root / "work"), "VIDPIPE_OUT_DIR": str(root / "out"),
    })
    if has_cjk_font():
        os.environ["VIDPIPE_SUBTITLE_FONT"] = font_name()
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

    tts.close()
    vg_server.close()
    print(json.dumps({"ok": True, "work_dir": str(root / "work"), "out_dir": str(root / "out"),
                      "shorts": ["taipei-night-01", "old-bookstore-02", "late-walk-03",
                                 "broken-04"],
                      "serve": f"VIDPIPE_WORK_DIR={root / 'work'} VIDPIPE_OUT_DIR={root / 'out'}"
                               " vidpipe serve --no-open"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
