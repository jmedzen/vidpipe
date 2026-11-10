"""設定：pipeline.json（進版控）＋ .env（不進版控，只放祕密與機器相關的值）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    """把 .env 的 KEY=VALUE 放進 os.environ（已有的環境變數優先）。不印任何值。"""
    p = path or ROOT / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load(path: Path | None = None) -> dict:
    load_env()
    cfg = json.loads((path or ROOT / "pipeline.json").read_text(encoding="utf-8"))
    cfg["root"] = str(ROOT)
    cfg["work"] = os.environ.get("PIPELINE_WORK", str(ROOT / "work"))
    cfg["out"] = os.environ.get("PIPELINE_OUT", str(ROOT / "out"))
    return cfg


def abspath(cfg: dict, p: str) -> str:
    q = Path(os.path.expanduser(p))
    return str(q if q.is_absolute() else Path(cfg["root"]) / q)
