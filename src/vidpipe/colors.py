"""字幕與疊字用的顏色：名稱或 #RRGGBB → ASS 色碼。"""

from __future__ import annotations

# 名稱 → RGB。white／yellow／paleyellow 是原本寫死的三個顏色，值不能改（改了舊片重跑會變色）。
COLORS: dict[str, str] = {
    "white": "FFFFFF", "yellow": "FFDD33", "paleyellow": "FFE066", "cyan": "66DDFF",
    "pink": "FF8FC0", "orange": "FFA040", "green": "8EE08A", "black": "000000",
}
BOX_COLOR = "&H60000000"     # 底框：黑色、約 60% 不透明


def is_color(value: object) -> bool:
    """顏色名稱（見 COLORS）或 #RRGGBB。"""
    if not isinstance(value, str):
        return False
    if value in COLORS:
        return True
    return len(value) == 7 and value[0] == "#" and all(
        c in "0123456789abcdefABCDEF" for c in value[1:])


def ass_color(value: str) -> str:
    """顏色名稱或 #RRGGBB → ASS 的 &H00BBGGRR（ASS 是 BGR）。"""
    rgb = COLORS.get(value) or value.lstrip("#")
    return f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}".upper()
