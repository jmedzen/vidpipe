"""旁白文字分句。

每一句會各自送去 TTS（量秒數就是字幕時間軸），所以切法直接決定字幕的節奏：
- 句末標點（。！？；!?;…）與換行一定切；標點後面的收尾引號、括號留在同一句。
- 句子太長（預設超過 40 字）就在逗號、頓號處切成幾段，每段盡量填滿但不超過上限——
  字幕一行放不下，TTS 長句也比較容易出錯。沒有逗號可切的長句維持原樣，不硬砍字。
- 只剩標點的碎片（例如單獨的「。」「……」）併回前一句。

「字數」一個中文字算 1，連續的英數字算 1 個詞的長度（`len`），不做斷詞。
"""

from __future__ import annotations

import re

from .errors import BadInput

DEFAULT_MAX_CHARS = 40

_TERMINATORS = "。！？；!?;…⋯"
_CLOSERS = "」』）)”’\"'》】〉]"
_COMMAS = "，、,：:"
_ABBREV = re.compile(r"\b(mr|mrs|ms|dr|prof|vs|st|jr|sr)$", re.IGNORECASE)
_SPEAKABLE = re.compile(r"[0-9A-Za-zÀ-ɏ぀-ヿ㐀-鿿가-힯]")


def has_speech(text: str) -> bool:
    """有沒有任何唸得出來的字（只有標點、空白的不算）。"""
    return bool(_SPEAKABLE.search(text))


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("　", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))


def _split_terminators(line: str) -> list[str]:
    """一行文字 → 依句末標點切開（標點與收尾引號留在句尾）。"""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        buf.append(ch)
        i += 1
        ends = ch in _TERMINATORS
        # 英文句點：後面是空白或結尾才算句末（避開 3.5、v1.2、example.com）
        if (ch == "." and (i >= n or line[i].isspace()) and i >= 2 and not line[i - 2].isdigit()
                and not _ABBREV.search(line[:i - 1])):
            ends = True
        if not ends:
            continue
        while i < n and (line[i] in _TERMINATORS or line[i] in _CLOSERS):
            buf.append(line[i])  # 「！！」「……」「？」」連在一起的標點與收尾引號
            i += 1
        out.append("".join(buf).strip())
        buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _pack_clauses(sentence: str, max_chars: int) -> list[str]:
    """過長的句子在逗號處切開，貪婪填滿到不超過 max_chars。"""
    if len(sentence) <= max_chars:
        return [sentence]
    clauses: list[str] = []
    buf: list[str] = []
    for ch in sentence:
        buf.append(ch)
        if ch in _COMMAS:
            clauses.append("".join(buf))
            buf = []
    if buf:
        clauses.append("".join(buf))
    packed: list[str] = []
    cur = ""
    for clause in clauses:
        if cur and len(cur) + len(clause) > max_chars:
            packed.append(cur.strip())
            cur = clause
        else:
            cur += clause
    if cur.strip():
        packed.append(cur.strip())
    return packed


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """旁白文字 → 句子清單（不含空句）。"""
    if max_chars < 8:
        raise BadInput(f"max_chars 太小（{max_chars}）", hint="至少 8；建議 30–45")
    sentences: list[str] = []
    for line in _clean(text).split("\n"):
        if not line:
            continue
        for piece in _split_terminators(line):
            if not piece:
                continue
            if not has_speech(piece):
                if sentences:  # 只有標點：併回前一句
                    sentences[-1] += piece
                continue
            sentences.extend(_pack_clauses(piece, max_chars))
    return sentences
