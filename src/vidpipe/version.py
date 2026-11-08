"""版本與 build number。

`version` 由人決定；`build` 是 git commit 數，每個 commit 都會動。
來源依序：VIDPIPE_BUILD 環境變數 → git（有 .git 才行）→ "dev"。
Docker 建置沒有 .git，所以用建置參數把 build 傳進來。
"""

from __future__ import annotations

import os
import subprocess
from importlib import metadata
from pathlib import Path

try:
    __version__ = metadata.version("vidpipe")
except metadata.PackageNotFoundError:  # 沒裝成套件（直接從原始碼跑）
    __version__ = "0.0.0+src"


def build() -> str:
    env = (os.environ.get("VIDPIPE_BUILD") or "").strip()
    if env:
        return env
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists():
        try:
            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", "-C", str(root), *args],
                    capture_output=True, text=True, timeout=5, check=True,
                ).stdout.strip()

            return f"{git('rev-list', '--count', 'HEAD')}.{git('rev-parse', '--short', 'HEAD')}"
        except Exception:  # noqa: BLE001 - 沒 git 或不是 repo，退回 dev
            pass
    return "dev"


def full_version() -> str:
    return f"{__version__}+{build()}"
