"""素材畫面裡燒進去的文字（浮水印、「訂閱」「下載」之類的宣傳字卡）偵測。

stock 影片常把網站名稱、宣傳字或浮水印直接壓在畫面上：不只難看，pond5 之類的浮水印還代表那是
未授權的預覽版。成片的中間那一帶（離開上方標題與下方字幕）不該有文字，用 tesseract 掃描；
沒有安裝 tesseract（例如 Docker 映像）時整個檢查略過、回 None，不影響出片。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

BAND = (0.22, 0.42)         # 從畫面高度的 22% 開始、取 42% 高（避開標題與字幕）
MIN_CONF = 70
MIN_WORD = 3
SAMPLES = (0.25, 0.75)      # 一個場景抽哪幾格（佔場景長度的比例）


def available() -> bool:
    return shutil.which("tesseract") is not None and shutil.which("ffmpeg") is not None


def _band_png(video: Path, at: float, dest: Path, env: dict[str, str] | None) -> bool:
    crop = f"crop=iw:ih*{BAND[1]}:0:ih*{BAND[0]}"
    run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video),
                          "-frames:v", "1", "-vf", crop, str(dest)],
                         capture_output=True, env=env, check=False)
    return run.returncode == 0 and dest.is_file()


def words_in(png: Path) -> list[str]:
    """這張圖裡辨識得很有把握的英數字（三個字元以上）。"""
    out = subprocess.run(["tesseract", str(png), "-", "--psm", "11", "-l", "eng", "tsv"],
                         capture_output=True, text=True, check=False).stdout
    found = []
    for line in out.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        word = cols[11].strip()
        if conf >= MIN_CONF and len(re.sub(r"[^A-Za-z0-9]", "", word)) >= MIN_WORD:
            found.append(word)
    return found


def suspicious(words: list[str]) -> bool:
    return len(words) >= 2 or sum(len(w) for w in words) >= 8


def scenes_with_text(video: str | Path, timeline: dict[str, Any],
                     env: dict[str, str] | None = None) -> list[dict[str, Any]] | None:
    """每個場景抽幾格，回畫面中間有文字的場景：`[{"scene", "words"}]`。

    沒有 tesseract 回 None（呼叫端當作沒檢查）。
    """
    if not available():
        return None
    flagged = []
    with tempfile.TemporaryDirectory() as d:
        for sc in timeline.get("scenes", []):
            words: list[str] = []
            span = float(sc.get("duration") or (float(sc["end"]) - float(sc["start"])))
            for n, frac in enumerate(SAMPLES):
                png = Path(d) / f"{sc['id']}-{n}.png"
                if _band_png(Path(video), float(sc["start"]) + span * frac, png, env):
                    words += words_in(png)
            if suspicious(words):
                flagged.append({"scene": sc["id"], "words": list(dict.fromkeys(words))[:8]})
    return flagged
