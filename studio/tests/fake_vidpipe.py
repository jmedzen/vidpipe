#!/usr/bin/env python3
"""假的 vidpipe:validate 回粗估秒數;make 用 ffmpeg 合成一支有聲音的片,
並寫出 timeline、字幕、sources。"""
import json
import os
import subprocess
import sys
from pathlib import Path

cmd, manifest_path = sys.argv[1], sys.argv[2]
m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
if cmd == "validate":
    print(json.dumps({"ok": True, "scenes": [{"id": s["id"], "estimated_seconds": 4.0}
                                             for s in m["scenes"]]}))
    sys.exit(0)
work = Path(os.environ["VIDPIPE_WORK_DIR"]) / "shorts" / m["id"]
out = Path(os.environ["VIDPIPE_OUT_DIR"])
work.mkdir(parents=True, exist_ok=True)
out.mkdir(parents=True, exist_ok=True)
per = float(os.environ.get("FAKE_SCENE_SECONDS", "4"))
total = per * len(m["scenes"])
w, h = (1920, 1080) if m.get("output", {}).get("aspect") == "16:9" else (1080, 1920)
video = out / f"{m['id']}.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=s={w}x{h}:r=30:d={total}", "-f", "lavfi", "-i",
                f"anoisesrc=d={total}:c=pink:a=0.5", "-af", "loudnorm=I=-14:TP=-2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-ar", "48000", "-shortest", str(video)], check=True)
scenes, srt = [], []
for i, s in enumerate(m["scenes"]):
    start = i * per + 0.2
    scenes.append({"id": s["id"], "start": i * per, "end": (i + 1) * per, "duration": per,
                   "sentences": [
        {"text": s["text"], "start": start, "end": start + per - 0.7}]})
    def ts(x):
        return f"00:{int(x // 60):02d}:{x % 60:06.3f}".replace(".", ",")
    srt.append(f"{i + 1}\n{ts(start)} --> {ts(start + per - 0.6)}\n{s['text']}\n")
(work / "timeline.json").write_text(json.dumps({"scenes": scenes}, ensure_ascii=False),
                                    encoding="utf-8")
(work / "subs.srt").write_text("\n".join(srt), encoding="utf-8")
(work / "sources.json").write_text(json.dumps({"scenes": [
    {"scene": s["id"], "offset": s["clip"]["start"], "looped": False,
     "source": {"kind": "asset", "asset_id": s["clip"]["asset_id"]}} for s in m["scenes"]],
    "music": None}), encoding="utf-8")
credits = out / f"{m['id']}.credits.md"
credits.write_text("# credits\n\n```\n素材來源:\nAlice — https://example.com/a1\n```\n",
                   encoding="utf-8")
(out / f"{m['id']}.cover.jpg").write_bytes(b"jpg")
print(json.dumps({"ok": True, "output": {"path": str(video), "duration": total, "warnings": [],
      "credits": {"path": str(credits), "attribution_required": ["youtube:a1"], "unclear": []}}}))
