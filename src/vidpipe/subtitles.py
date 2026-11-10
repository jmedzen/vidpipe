"""字幕：時間軸 → SRT（給播放器／上傳）與 ASS（燒進影片）。

時間直接用 `timeline` 裡每一句旁白的起訖，所以字幕跟聲音對得上。每一句：

- **分頁**：一個畫面最多兩行中文。太長的句子（40 字的長句大約 3 行）先在逗號附近切成
  幾頁，每頁的顯示時間依字數比例分配——與其擠成三行小字，不如分成兩次出現。
- **折行**：中文沒有空白可以斷，所以自己折：優先在逗號、頓號後面斷，兩行盡量一樣長
  （不要「14 字＋1 字」），遵守避頭尾（`，。！？」` 不開頭，`「（` 不結尾），英文與數字
  不從單字中間斷開。
- **雙語**（`zh-TW+en`）：中文在上、英文在下（較小、淡黃）。句子分成幾頁時，英文的單字
  依各頁中文字數比例分配。沒有英文的句子只顯示中文，並在 `warnings` 提醒。
- **停留**：最後一頁在聲音結束後多停 0.15 秒（不超過下一句的開始），太短的句子至少
  顯示 0.4 秒，所以不會閃一下就消失；任何兩條字幕都不重疊。
- **位置**：字幕放在底部，但留出 Shorts 介面（標題、頻道名）會蓋住的空間：直式（9:16）
  離底邊 20%，橫式 8%。字級與每行字數都跟輸出寬度成比例，不同長寬比都適用。

字體預設 `Noto Sans CJK TC`（Docker 裡有裝；Mac 要自己裝，或用 `subtitle_font` 指定）。
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .manifest import Subtitles
from .video import OutputSpec

if TYPE_CHECKING:
    from .timeline import Timeline

HOLD = 0.15          # 聲音結束後多停多久
MIN_SHOWN = 0.4      # 每條字幕至少顯示多久
MAX_LINES = 2        # 一個畫面最多幾行中文
EN_SCALE = 0.62      # 英文字級 / 中文字級
LATIN_EM = 0.58      # 一個英數字大約是中文字寬的幾倍（粗體無襯線的平均）
SPACE_EM = 0.3
CLAUSE_BONUS = 6.0   # 在逗號後面斷，可以容忍兩邊差到這麼多個字寬（寧可不均勻也不要斷在詞中間）

# 避頭尾：這些字不放在行首；開括號不放在行尾
_NO_START = set("，。、！？；：）」』》】〉”’…—‥⋯,.!?;:)]}%％")
_NO_END = set("（「『《【〈“‘([{")
_CLAUSE = set("，、；：,;:。！？!?")
_WIDE_AMBIGUOUS = set("…—‥⋯·")  # 東亞寬度標成「模稜兩可」，但中文字型裡是全形


# ------------------------------------------------------------------ 版面

@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    font_size: int
    en_size: int
    outline: int
    margin_side: int
    margin_bottom: int

    @property
    def usable(self) -> int:
        return self.width - 2 * self.margin_side

    @property
    def zh_em(self) -> float:
        """一行中文最多幾個「全形字寬」。"""
        return self.usable / self.font_size

    @property
    def en_em(self) -> float:
        return self.usable / self.en_size


BASE_SIZE = 0.06             # 字級佔短邊的比例（normal）
SIZE_SCALE = {"normal": 1.0, "large": 1.25, "xlarge": 1.5}


def layout_for(spec: OutputSpec, size_name: str = "normal") -> Layout:
    # 以短邊為準：橫式不會變成巨大的字
    size = round(min(spec.width, spec.height) * BASE_SIZE * SIZE_SCALE[size_name])
    portrait = spec.height > spec.width
    return Layout(width=spec.width, height=spec.height, font_size=size,
                  en_size=round(size * EN_SCALE), outline=max(2, round(size * 0.08)),
                  margin_side=round(spec.width * 0.07),
                  margin_bottom=round(spec.height * (0.20 if portrait else 0.08)))


# ------------------------------------------------------------------ 折行

@dataclass(frozen=True)
class _Atom:
    text: str
    em: float
    space: bool = False


def _char_em(ch: str) -> float:
    if unicodedata.east_asian_width(ch) in ("W", "F") or ch in _WIDE_AMBIGUOUS:
        return 1.0
    return LATIN_EM


def _atoms(text: str) -> list[_Atom]:
    """中文字一個字一個單位；連續的英數字（一個單字）是一個單位，不會被從中間斷開。"""
    atoms: list[_Atom] = []
    word = ""
    for ch in text:
        if _char_em(ch) != 1.0 and not ch.isspace():  # 半形字（含半形標點）黏成一個單字
            word += ch
            continue
        if word:
            atoms.append(_Atom(word, sum(_char_em(c) for c in word)))
            word = ""
        if ch.isspace():
            if atoms and not atoms[-1].space:
                atoms.append(_Atom(" ", SPACE_EM, space=True))
        else:
            atoms.append(_Atom(ch, _char_em(ch)))
    if word:
        atoms.append(_Atom(word, sum(_char_em(c) for c in word)))
    return atoms


def _can_break(atoms: list[_Atom], i: int) -> bool:
    """可以在 atoms[i-1] 與 atoms[i] 之間斷行嗎？"""
    before, after = atoms[i - 1], atoms[i]
    if before.space or after.space:
        return True
    return after.text[0] not in _NO_START and before.text[-1] not in _NO_END


def _join(atoms: list[_Atom]) -> str:
    return "".join(a.text for a in atoms).strip()


def wrap(text: str, max_em: float) -> list[str]:
    """折成幾行，每行不超過 max_em 個全形字寬；盡量均勻、盡量在逗號後面斷。"""
    atoms = _atoms(text.strip())
    if not atoms:
        return []
    lines: list[str] = []
    start = 0
    while start < len(atoms):
        rest = sum(a.em for a in atoms[start:])
        left = max(1, math.ceil(rest / max_em - 1e-9))
        if left == 1:
            lines.append(_join(atoms[start:]))
            break
        target = rest / left
        best, best_score, width = None, math.inf, 0.0
        for end in range(start + 1, len(atoms)):
            width += atoms[end - 1].em
            if width > max_em + 1e-9:
                break
            if not _can_break(atoms, end):
                continue
            if rest - width > (left - 1) * max_em + 1e-9:  # 剩下的行放不下
                continue
            score = abs(width - target) - (CLAUSE_BONUS if atoms[end - 1].text in _CLAUSE else 0.0)
            if score < best_score:
                best, best_score = end, score
        if best is None:  # 規則排不出來（例如整行都是超長英文單字）：盡量塞，至少一個單位
            width, best = 0.0, start + 1
            for end in range(start + 1, len(atoms)):
                width += atoms[end - 1].em
                if width > max_em + 1e-9:
                    break
                best = end
        lines.append(_join(atoms[start:best]))
        start = best
        while start < len(atoms) and atoms[start].space:
            start += 1
    return [line for line in lines if line]


def _em(text: str) -> float:
    return sum(_char_em(c) for c in text if not c.isspace())


def paginate(text: str, max_em: float, max_lines: int = MAX_LINES) -> list[list[str]]:
    """一句 → 幾頁，每頁最多 max_lines 行。先切頁（用兩行的寬度當上限）再各自折行。"""
    pages = wrap(text, max_em * max_lines)
    return [wrap(page, max_em) for page in pages]


def _split_words(text: str, weights: list[float]) -> list[str]:
    """把英文依各頁中文字數的比例分給每一頁（在單字邊界切）。

    按比例算出來的切點附近（±20% 的字數）如果有逗號、分號、句號結尾的單字，就切在那裡——
    英文才不會斷成 "crowds keep pouring | in, every stall"。
    """
    words = text.split()
    if len(weights) == 1:
        return [" ".join(words)]
    total = sum(weights)
    radius = max(2, round(len(words) * 0.2))
    cuts: list[int] = []
    cum = 0.0
    for w in weights[:-1]:
        cum += w
        ideal = round(len(words) * cum / total)
        floor = cuts[-1] if cuts else 0
        near = [j for j in range(max(1, ideal - radius), min(len(words), ideal + radius + 1))
                if words[j - 1][-1] in ",;:.!?" and j > floor]
        cuts.append(min(near, key=lambda j: abs(j - ideal)) if near else max(ideal, floor))
    bounds = [0, *cuts, len(words)]
    return [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:], strict=False)]


# ------------------------------------------------------------------ 排成字幕

@dataclass(frozen=True)
class Entry:
    start: float
    end: float
    zh: tuple[str, ...]
    en: tuple[str, ...] = ()


@dataclass(frozen=True)
class Subs:
    entries: tuple[Entry, ...]
    srt: str
    ass: str
    warnings: tuple[str, ...] = ()


def entries_for(timeline: Timeline, subtitles: Subtitles, layout: Layout) -> tuple[
        list[Entry], list[str]]:
    bilingual = subtitles.langs == "zh-TW+en"
    cues = timeline.cues
    total = timeline.total
    entries: list[Entry] = []
    missing: list[str] = []
    for n, cue in enumerate(cues):
        pages = paginate(cue.text, layout.zh_em)
        if not pages:
            continue
        weights = [max(0.1, _em("".join(p))) for p in pages]
        english = [""] * len(pages)
        if bilingual:
            if cue.en.strip():
                english = _split_words(cue.en, weights)
            else:
                missing.append(cue.scene_id)
        limit = cues[n + 1].start if n + 1 < len(cues) else total
        end = min(max(cue.end + HOLD, cue.start + MIN_SHOWN), limit, total)
        end = max(end, cue.end)  # 聲音還沒念完就消失更糟，寧可跟下一句重疊也不截斷
        span = end - cue.start
        cum = 0.0
        for k, (page, en_text) in enumerate(zip(pages, english, strict=True)):
            a = cue.start + span * cum / sum(weights)
            cum += weights[k]
            b = cue.start + span * cum / sum(weights)
            en_lines = tuple(wrap(en_text, layout.en_em)) if en_text else ()
            entries.append(Entry(round(a, 3), round(b, 3), tuple(page), en_lines))
    warnings: list[str] = []
    if missing:
        scenes = sorted(set(missing), key=missing.index)
        warnings.append(f"雙語字幕：{len(missing)} 句沒有 en，只顯示中文"
                        f"（場景 {', '.join(scenes[:5])}{'…' if len(scenes) > 5 else ''}）")
    return entries, warnings


# ------------------------------------------------------------------ 格式

def _srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(entries: list[Entry]) -> str:
    blocks = []
    for i, e in enumerate(entries, 1):
        text = "\n".join((*e.zh, *e.en))
        blocks.append(f"{i}\n{_srt_time(e.start)} --> {_srt_time(e.end)}\n{text}\n")
    return "\n".join(blocks)


def _ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(line: str) -> str:
    # `{` 會被當成覆寫標籤的開頭；口白裡幾乎不會有，換成全形最安全
    return line.replace("{", "｛").replace("}", "｝").replace("\\", "＼")


OVERLAY_SCALE = {"xlarge": 1.6, "large": 1.2, "medium": 1.0, "small": 0.6}
OVERLAY_COLOR = {"white": "&H00FFFFFF", "yellow": "&H0033DDFF"}   # ASS 是 BGR
OVERLAY_MAX_LINES = 3


def _overlay_margin(layout: Layout, position: str) -> int:
    """疊字離上／下緣多遠：避開 Shorts 的介面，也不跟字幕（離底 20%／8%）疊在一起。"""
    portrait = layout.height > layout.width
    if position == "top":
        return round(layout.height * (0.09 if portrait else 0.05))
    return round(layout.height * (0.115 if portrait else 0.02))


def _overlay_lines(overlays: Sequence[dict[str, str]], layout: Layout, total: float) -> tuple[
        list[str], list[str]]:
    """常駐疊字 → (ASS 樣式行, ASS 事件行)。從第 0 秒顯示到片尾，放在字幕下面那一層。"""
    styles, events = [], []
    for i, o in enumerate(overlays):
        # 疊字的大小以 normal 字級為準，不跟著 subtitles.size 一起放大
        base = min(layout.width, layout.height) * BASE_SIZE
        size = round(base * OVERLAY_SCALE[o["size"]])
        align = 8 if o["position"] == "top" else 2
        color = OVERLAY_COLOR[o["color"]]
        styles.append(
            f"Style: Overlay{i},{{font}},{size},{color},{color},&H00000000,&H78000000,-1,0,0,0,"
            f"100,100,0,0,1,{max(2, round(size * 0.08))},1,{align},{layout.margin_side},"
            f"{layout.margin_side},{_overlay_margin(layout, o['position'])},1")
        lines = wrap(o["text"], layout.usable / size)[:OVERLAY_MAX_LINES]
        text = "\\N".join(_ass_text(x) for x in lines)
        events.append(f"Dialogue: 0,{_ass_time(0)},{_ass_time(total)},Overlay{i},,0,0,0,,{text}")
    return styles, events


def to_ass(entries: list[Entry], layout: Layout, font: str,
           overlays: Sequence[dict[str, str]] = (), total: float = 0.0) -> str:
    def style(name: str, size: int, color: str) -> str:
        # Bold=-1、BorderStyle=1（描邊）、Alignment=2（底部置中）
        return (f"Style: {name},{font},{size},{color},{color},&H00000000,&H78000000,-1,0,0,0,"
                f"100,100,0,0,1,{layout.outline},1,2,{layout.margin_side},{layout.margin_side},"
                f"{layout.margin_bottom},1")

    overlay_styles, overlay_events = _overlay_lines(overlays, layout, total)
    lines = [
        "[Script Info]",
        "; 由 vidpipe 產生",
        "ScriptType: v4.00+",
        f"PlayResX: {layout.width}",
        f"PlayResY: {layout.height}",
        "WrapStyle: 2",            # 不自動折行：折行由我們決定
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style("Default", layout.font_size, "&H00FFFFFF"),
        style("En", layout.en_size, "&H0066E0FF"),
        *(line.replace("{font}", font) for line in overlay_styles),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines += overlay_events
    for e in entries:
        text = "\\N".join(_ass_text(x) for x in e.zh)
        if e.en:
            text += "\\N{\\rEn}" + "\\N".join(_ass_text(x) for x in e.en)
        lines.append(f"Dialogue: 0,{_ass_time(e.start)},{_ass_time(e.end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def build(timeline: Timeline, subtitles: Subtitles, spec: OutputSpec, font: str,
          overlays: Sequence[dict[str, str]] = ()) -> Subs:
    """字幕檔。`overlays`（manifest 的常駐疊字）只進 `.ass`（燒進畫面的那一份），不進 `.srt`。"""
    layout = layout_for(spec, subtitles.size)
    entries, warnings = entries_for(timeline, subtitles, layout)
    return Subs(tuple(entries), to_srt(entries),
                to_ass(entries, layout, font, overlays, timeline.total), tuple(warnings))
