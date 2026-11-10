"""credits.md：這支片用了哪些素材，作者、授權、連結，加一段可以直接貼進說明欄的文字。

資料來自 `render` 寫的 `sources.json`。同一個素材用在好幾個場景只列一次（場景合併寫）。

- 授權 `free-attribution`（例如 CC BY）要標註作者，列在「必須標註」。
- 授權 `check`／`unknown`（vidgrab 無法確定）標 ⚠️，公開前先確認。
- 本機檔案沒有授權資料，一律提醒自己確認。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_DECLARED = {"own": "自製素材（manifest 宣告）", "free": "可自由使用（manifest 宣告）",
             "free-attribution": "可用需標註（manifest 宣告）"}


def _license_label(credit: dict[str, Any]) -> tuple[str, str]:
    """(顯示用的授權說明, free／free-attribution／check／unknown／file)。"""
    if credit.get("kind") == "file":
        declared = credit.get("declared_license")
        if declared in _DECLARED:      # manifest 寫了 license：照宣告的算，不再標 ⚠️
            return _DECLARED[declared], "free-attribution" if declared == "free-attribution" \
                else "free"
        return "本機檔案，授權請自行確認", "file"
    lic = credit.get("license") or {}
    kind = lic.get("kind") or "unknown"
    label = lic.get("label") or lic.get("name") or "授權未明"
    name = lic.get("name")
    text = f"{label}（{name}）" if name and name != label else label
    return text, kind


def _title(credit: dict[str, Any]) -> str:
    if credit.get("kind") == "file":
        return Path(str(credit.get("path") or "")).name or "（本機檔案）"
    return credit.get("title") or credit.get("asset_id") or "（未命名）"


def _credit_text(credit: dict[str, Any]) -> str:
    """說明欄用的一行：優先用 vidgrab 寫好的 credit_hint。"""
    hint = credit.get("credit_hint") or credit.get("declared_credit")
    if hint:
        return str(hint)
    parts = [credit.get("author") or "", credit.get("webpage_url") or ""]
    return " — ".join(p for p in parts if p) or _title(credit)


def _entry(credit: dict[str, Any], used_in: str) -> str:
    license_text, kind = _license_label(credit)
    flag = "⚠️ " if kind in ("check", "unknown", "file") else ""
    line = f"- 「{_title(credit)}」"
    if credit.get("author") or credit.get("declared_credit"):
        line += f"— {credit.get('author') or credit['declared_credit']}"
    line += f"　授權：{flag}{license_text}"
    if kind == "free-attribution":
        line += "（**必須標註**）"
    if credit.get("webpage_url"):
        line += f"　{credit['webpage_url']}"
    return f"{line}　（{used_in}）"


def _key(credit: dict[str, Any]) -> str:
    return str(credit.get("asset_id") or credit.get("path") or credit.get("webpage_url") or "")


def build(short_id: str, title: str, sources: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """sources.json 的內容 → (credits.md 全文, 摘要)。

    摘要：`assets`（不重複的素材數）、`attribution_required`（要標註的 asset id）、
    `unclear`（授權未明或本機檔案的 id／路徑）。
    """
    videos: dict[str, tuple[dict[str, Any], list[str]]] = {}
    for scene in sources.get("scenes") or []:
        credit = scene.get("source")
        if credit:
            videos.setdefault(_key(credit), (credit, []))[1].append(scene["scene"])
    music = (sources.get("music") or {}).get("source")

    lines = [f"# 素材來源與授權：{title or short_id}", "", f"作品 id：`{short_id}`", ""]
    if videos:
        lines += ["## 影片素材", ""]
        lines += [_entry(c, "場景 " + "、".join(scenes)) for c, scenes in videos.values()]
        lines.append("")
    if music:
        lines += ["## 背景音樂", "", _entry(music, "背景音樂"), ""]
    if not videos and not music:
        lines += ["這支片沒有用到外部素材。", ""]

    everything = [c for c, _ in videos.values()] + ([music] if music else [])
    hints = list(dict.fromkeys(_credit_text(c) for c in everything
                               if c.get("kind") != "file" or c.get("declared_credit")))
    if hints:
        lines += ["## 說明欄範本", "", "```", "素材來源："]
        lines += hints
        lines += ["```", "", "（標 ⚠️ 的授權未明，公開前請先確認；`必須標註` 的一定要放。）", ""]

    attribution, unclear = [], []
    for c in everything:
        _, kind = _license_label(c)
        who = str(c.get("asset_id") or c.get("path") or "")
        if kind == "free-attribution":
            attribution.append(who)
        elif kind in ("check", "unknown", "file"):
            unclear.append(who)
    summary = {"assets": len(everything), "attribution_required": attribution,
               "unclear": unclear}
    return "\n".join(lines).rstrip("\n") + "\n", summary
