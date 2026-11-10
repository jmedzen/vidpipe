"""腳本的規則檢查(不靠 LLM):字數、句長、禁用語、出處、簡體字。"""
from __future__ import annotations

import re

from vidpipe import quick

PUNCT = re.compile(r"[\s，。！？；：、「」『』（）《》〈〉…—\-,.!?;:'\"()]+")
SENT_END = re.compile(r"[。！？!?；;…\n]+")
BANNED_OPENERS = ("大家好", "今天我們來聊", "今天要跟大家", "哈囉", "嗨大家", "歡迎來到",
                  "歡迎回到")
BANNED_PHRASES = ("保證", "一定會好運", "靈驗", "消災解厄", "業障消除", "趕快訂閱", "按讚訂閱",
                  "捐款", "供養金", "報應就會", "不信的話")
normalize = quick.normalize      # 標點轉全形(跟文案直出同一套)


def normalize_script(script: dict) -> dict:
    for key in ("title", "hook", "closing", "practice"):
        if isinstance(script.get(key), str):
            script[key] = normalize(script[key])
    script["hooks"] = [normalize(h) for h in script.get("hooks", [])]
    groups = [script] + list(script.get("chapters", []))
    for group in groups:
        for sc in group.get("scenes", []):
            sc["text"] = normalize(sc.get("text", ""))
        if isinstance(group.get("title"), str):
            group["title"] = normalize(group["title"])
    return script


def chars(text: str) -> int:
    return len(PUNCT.sub("", text))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_END.split(text) if chars(s)]


def _simplified_only(ch: str) -> bool:
    """GB2312 有、Big5 沒有的字 = 只在簡體出現的字形(不用維護字表,也不會誤判繁簡同形的字)。"""
    if not "\u4e00" <= ch <= "\u9fff":
        return False
    try:
        ch.encode("big5")
        return False
    except UnicodeEncodeError:
        pass
    try:
        ch.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


def simplified_found(text: str) -> list[str]:
    return sorted({c for c in text if _simplified_only(c)})


def flatten(script: dict) -> list[dict]:
    """腳本 → 場景清單(長影片把各章攤平,帶章節標題)。"""
    if script.get("chapters"):
        out = []
        for ci, ch in enumerate(script["chapters"], 1):
            for sc in ch.get("scenes", []):
                out.append({**sc, "chapter": ch.get("title", ""), "chapter_index": ci})
        return out
    return list(script.get("scenes", []))


def lint(script: dict, *, min_chars: int, max_chars: int, themes: set[str],
         max_sentence: int = 20) -> dict:
    issues: list[dict] = []

    def add(severity: str, where: str, problem: str) -> None:
        issues.append({"severity": severity, "where": where, "problem": problem})

    scenes = flatten(script)
    if not scenes:
        add("hard", "scenes", "沒有任何場景")
    total = sum(chars(s.get("text", "")) for s in scenes)
    if not min_chars <= total <= max_chars:
        add("hard", "全片", f"旁白 {total} 字,要在 {min_chars}–{max_chars} 字之間")
    first = scenes[0].get("text", "") if scenes else ""
    if any(first.startswith(o) or o in first[:12] for o in BANNED_OPENERS):
        add("hard", "s1", "開頭是寒暄,要直接用問題或反直覺的斷言開場")
    prev = None
    for i, sc in enumerate(scenes, 1):
        where = f"s{i}"
        text = sc.get("text", "")
        if not chars(text):
            add("hard", where, "沒有旁白文字")
        for sent in sentences(text):
            if chars(sent) > max_sentence:
                add("soft", where, f"句子超過 {max_sentence} 字({chars(sent)} 字):{sent}")
        if re.search(r"[0-9A-Za-z]", text):
            add("soft", where, "旁白含阿拉伯數字或英文,語音合成容易唸錯")
        for phrase in BANNED_PHRASES:
            if phrase in text:
                add("hard", where, f"含禁用語「{phrase}」")
        simp = simplified_found(text)
        if simp:
            add("hard", where, "疑似簡體字:" + "".join(simp))
        if sc.get("visual") not in themes:
            add("hard", where, f"visual「{sc.get('visual')}」不在主題清單裡")
        elif sc.get("visual") == prev:
            add("soft", where, "與上一個場景用同一個畫面主題")
        prev = sc.get("visual")
        if chars(text) > 70:
            add("soft", where, f"單一場景 {chars(text)} 字太長,畫面會停太久")
    if not script.get("sources"):
        add("hard", "sources", "沒有標明出處")
    title = script.get("title", "")
    if not title or chars(title) > 20:
        add("hard", "title", "常駐標題要有,而且不超過 20 字")
    hard = [i for i in issues if i["severity"] == "hard"]
    return {"ok": not hard, "chars": total, "scenes": len(scenes), "issues": issues}
