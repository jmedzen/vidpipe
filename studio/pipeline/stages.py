"""流水線的各個階段。每個階段讀寫 work/<run_id>/ 底下的檔案,做完的不重做。"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from . import footage, llm, qc, textcheck, vidpipe
from .config import abspath

FORMAT_NAME = {"short": "直式 Shorts(9:16)", "long": "橫式長影片(16:9)"}

STRUCTURE_SHORT = {
    60: """1. Hook(第一句):生活化的痛點、疑問或反直覺的說法。
2. 共鳴:一個人人都經歷過的日常場景,兩三句。
3. 佛法觀點:用白話講一個核心觀念。
4. 轉化:一個現在就能做的小練習,或一個換位思考的角度。
5. 收束:一句金句。
共 5 到 7 個場景。""",
    90: """1. Hook(第一句):生活化的痛點、疑問或反直覺的說法。
2. 共鳴:一個日常場景。
3. 佛法觀點:用白話講一個核心觀念,加一個短故事、比喻或第二個生活例子。
4. 大約三十秒處要有一次轉折(新問題、反轉或情緒變化)。
5. 中段預告後面的內容(例如:最後這個方法,很多人都做反了)。
6. 轉化:一個小練習。
7. 收束:金句。
共 8 到 11 個場景。""",
    120: """1. Hook(第一句)。
2. 共鳴:一個日常場景。
3. 是什麼:用白話講核心觀念。
4. 為什麼會這樣:一個故事、比喻或第二個例子。
5. 怎麼做:一個具體的小練習,分兩三步。
6. 每十五到二十秒要有一個小鉤子或轉折;中段要預告後面的內容。
7. 收束:金句。
共 11 到 15 個場景。""",
}


def _prompt_part(name: str) -> str:
    """提示詞的片段(長影片的結構說明、輸出格式)放在 prompts/ 底下,跟其他提示詞一起版本控管。"""
    path = Path(__file__).resolve().parent.parent / "prompts" / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()


class Rejected(Exception):
    """腳本審稿多輪不過,或品質檢查硬性失敗。"""


def render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


class Run:
    def __init__(self, cfg: dict, topic: dict, fmt: str, seconds: int, date: str | None = None):
        self.cfg, self.topic, self.fmt, self.seconds = cfg, topic, fmt, seconds
        self.date = date or dt.date.today().strftime("%Y%m%d")
        self.id = f"{self.date}_{topic['id']}_{fmt}"
        self.short_id = f"{topic['id']}-{fmt}-{self.date}"
        self.dir = Path(cfg["work"]) / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.root = Path(cfg["root"])
        self.library = footage.load_library(self.root / "assets" / "footage.json")
        self.logfile = self.dir / "run.log"

    def log(self, message: str) -> None:
        line = f"{dt.datetime.now().strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with self.logfile.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---- 1 brief
    def brief(self) -> dict:
        cps = self.cfg["chars_per_second"]
        target = self.seconds * cps
        data = {"run_id": self.id, "topic": self.topic, "format": self.fmt,
                "target_seconds": self.seconds,
                "min_chars": int(target * 0.86), "max_chars": int(target * 1.08)}
        write(self.dir / "brief.json", data)
        return data

    # ---- 2–4 script + lint + review(不過就帶意見重寫)
    def _script_prompt(self, brief: dict, feedback: str) -> str:
        if self.fmt == "long":
            chapters = max(6, min(10, round(self.seconds / 65)))
            structure = _prompt_part("structure_long").format(chapters=chapters)
            schema = _prompt_part("schema_long")
        else:
            bucket = 60 if self.seconds <= 60 else 90 if self.seconds <= 90 else 120
            structure, schema = STRUCTURE_SHORT[bucket], _prompt_part("schema_short")
        t = self.topic
        return render((self.root / "prompts" / "script.md").read_text(encoding="utf-8"),
                      format_name=FORMAT_NAME[self.fmt], name=t["name"], concept=t["concept"],
                      sources="\n".join(f"  - {s}" for s in t["sources"]),
                      scenes="、".join(t.get("scenes", [])), emotion=t.get("emotion", ""),
                      seconds=self.seconds, cps=self.cfg["chars_per_second"],
                      min_chars=brief["min_chars"], max_chars=brief["max_chars"],
                      structure=structure, feedback=feedback,
                      motifs="、".join(self.library["themes"]), schema=schema)

    def script(self, brief: dict) -> dict:
        final = self.dir / "script.json"
        if final.is_file():
            return textcheck.normalize_script(read(final))
        feedback = ""
        rounds = self.cfg.get("review_max_rounds", 3)
        for n in range(1, rounds + 1):
            script = textcheck.normalize_script(
                llm.ask(self.dir, f"script.r{n}", self._script_prompt(brief, feedback)))
            lint = textcheck.lint(script, min_chars=brief["min_chars"],
                                  max_chars=brief["max_chars"],
                                  themes=set(self.library["themes"]))
            write(self.dir / f"lint.r{n}.json", lint)
            problems = [i for i in lint["issues"] if i["severity"] == "hard"]
            review = None
            if lint["ok"]:
                t = self.topic
                review = llm.ask(self.dir, f"review.r{n}", render(
                    (self.root / "prompts" / "review.md").read_text(encoding="utf-8"),
                    name=t["name"], concept=t["concept"],
                    sources="\n".join(f"  - {s}" for s in t["sources"]),
                    script=json.dumps(script, ensure_ascii=False, indent=1),
                    lint=json.dumps(lint, ensure_ascii=False)))
                scores = review.get("scores") or {}
                issues = review.get("issues", [])
                doubts = review.get("doubts") or []
                passed = bool(review.get("pass")) and not doubts and \
                    all(isinstance(v, (int, float)) and v >= 4 for v in scores.values()) and \
                    not any(i.get("severity") == "hard" for i in issues)
                # 第一版通過但還有不少可改之處:只多磨一輪(審稿人每次都挑得出小毛病,不能無限磨)
                polish = passed and len(issues) > 2 and n == 1 and rounds > 1
                self.log(f"審稿第 {n} 輪:{'通過' if passed else '不通過'}"
                         f"{'(再磨一輪)' if polish else ''} {scores},{len(issues)} 個意見")
                if passed and not polish:
                    script["review"] = {"round": n, "scores": scores,
                                        "summary": review.get("summary", ""),
                                        "open_issues": issues}
                    script["lint"] = {"chars": lint["chars"], "scenes": lint["scenes"]}
                    write(final, script)
                    return script
                if doubts and n == rounds:      # 改到最後一輪仍有疑義:交給人判斷
                    write(self.dir / "DOUBTS.json", doubts)
                    raise Rejected("審稿人對佛法內容有重大疑義,需要人來判斷:" +
                                   ";".join(map(str, doubts)))
                issues = issues + [{"severity": "hard", "where": "佛法疑義",
                                    "problem": str(d),
                                    "fix": "改成各傳統都沒有爭議的說法,或拿掉這個說法"}
                                   for d in doubts]
                review["issues"] = issues
                problems = review.get("issues", [])
            else:
                self.log(f"腳本第 {n} 輪規則檢查不過:{[p['problem'] for p in problems][:3]}")
            feedback = "\n# 上一版被退回,請針對下面每一點修改後重寫整份腳本\n" + "\n".join(
                f"- [{p.get('where', '')}] {p.get('problem', '')}" +
                (f" → {p['fix']}" if p.get("fix") else "") for p in problems) + \
                f"\n(上一版旁白共 {lint['chars']} 字)\n"
        raise Rejected(f"腳本 {rounds} 輪都沒通過審稿,見 {self.dir}")

    def beats(self, script: dict) -> list[dict]:
        """場景切成鏡頭(每個鏡頭一段畫面)。id 是 s<場景>-<鏡頭>。"""
        out = []
        for i, sc in enumerate(textcheck.flatten(script), 1):
            for j, text in enumerate(textcheck.split_beats(sc["text"]), 1):
                out.append({"id": f"s{i}-{j}", "text": text, "visual": sc["visual"]})
        return out

    # ---- 5 畫面
    def visuals(self, script: dict, banned: set[str] | None = None) -> list[dict]:
        out = self.dir / "footage.json"
        if out.is_file() and not banned:
            return read(out)
        cps = self.cfg["chars_per_second"]
        want = [{"id": b["id"], "visual": b["visual"],
                 "seconds": textcheck.chars(b["text"]) / cps + 2.0} for b in self.beats(script)]
        vcfg = self.cfg["visuals"]
        vg = footage.Vidgrab(self.cfg["vidpipe"]["env"]["VIDPIPE_VIDGRAB_URL"])
        pools = {}
        for theme in sorted({w["visual"] for w in want}):
            pools[theme] = footage.pool(vg, self.library, theme,
                                        Path(self.cfg["work"]) / "_footage",
                                        allow_unknown=vcfg.get("allow_unknown_license", False))
            if not pools[theme]:        # 這個主題沒有素材:借用相近的主題,不讓整支片失敗
                for other in self.library.get("fallback", ["sky", "river", "mountain", "leaves"]):
                    pools[theme] = footage.pool(
                        vg, self.library, other, Path(self.cfg["work"]) / "_footage",
                        allow_unknown=vcfg.get("allow_unknown_license", False))
                    if pools[theme]:
                        self.log(f"素材池 {theme} 是空的,改用 {other}")
                        break
            self.log(f"素材池 {theme}:{len(pools[theme])} 支")
        picks = footage.assign(want, pools, self.id, banned)
        write(out, picks)
        return picks

    # ---- 6 manifest
    def manifest(self, script: dict, picks: list[dict]) -> dict:
        scenes = []
        for sc, pick in zip(self.beats(script), picks, strict=True):
            scenes.append({"id": sc["id"], "text": sc["text"],
                           "clip": {"asset_id": pick["asset_id"], "start": pick["start"]},
                           "fit": "crop"})
        m = {"schema": "vidpipe.manifest/1", "id": self.short_id, "title": script["title"],
             "output": {"aspect": "16:9" if self.fmt == "long" else "9:16", "fps": 30},
             "voice": self.cfg["voice"], "timing": self.cfg["timing"], "scenes": scenes,
             "subtitles": {"langs": "zh-TW", "burn_in": True},
             "cover": {"text": script["title"]}}
        if self.cfg.get("overlays", True):      # 常駐標題與系列名稱(vidpipe 的 overlays)
            if self.fmt == "short":
                m["overlays"] = [{"text": script["title"], "position": "top", "size": "large",
                                  "color": "yellow"},
                                 {"text": self.cfg["series"], "position": "bottom",
                                  "size": "small"}]
            else:
                m["overlays"] = [{"text": self.cfg["series"] + "|" + script["title"],
                                  "position": "top", "size": "small"}]
        music = self.cfg.get("music")
        if music and music.get("tracks"):       # 每支片固定挑一首(依 run id),重跑不會換
            tracks = music["tracks"]
            pick = tracks[int(footage._unit(self.id, "music") * len(tracks)) % len(tracks)]
            m["music"] = {"asset_id": pick, "start": music.get("start", 0),
                          "volume": music.get("volume", 0.22),
                          "duck_under_voice": music.get("duck_under_voice", True)}
        write(self.dir / "manifest.json", m)
        return m

    # ---- 7 make
    def make(self) -> dict:
        path = str(self.dir / "manifest.json")
        check = vidpipe.run(self.cfg, "validate", path, log=self.logfile)
        est = sum(s.get("estimated_seconds", 0) for s in check.get("scenes", []))
        self.log(f"validate 通過,粗估 {est:.0f} 秒")
        result = vidpipe.run(self.cfg, "make", path, log=self.logfile)
        out = result["output"]
        write(self.dir / "make.json", out)
        self.log(f"成片 {out['duration']} 秒,警告 {len(out.get('warnings', []))} 條")
        return out

    # ---- 8 package
    def package(self, script: dict, made: dict) -> dict:
        final = self.dir / "package.json"
        if not final.is_file():
            slim = {k: script[k] for k in ("title", "hook", "closing", "practice", "sources")
                    if k in script}
            slim["text"] = "".join(s["text"] for s in textcheck.flatten(script))
            data = llm.ask(self.dir, "package", render(
                (self.root / "prompts" / "package.md").read_text(encoding="utf-8"),
                format_name=FORMAT_NAME[self.fmt],
                script=json.dumps(slim, ensure_ascii=False, indent=1),
                shorts_hint=",其中一個是 #Shorts" if self.fmt == "short" else ""))
            write(final, data)
        data = read(final)
        credits_md = Path(made["credits"]["path"]).read_text(encoding="utf-8")
        block = credits_md.split("```")[1].strip() if "```" in credits_md else ""
        # 說明欄範本只列作者與頁面;把需要標註的素材 id 對應的頁面一併確認在內
        lines = [data.get("summary", ""), "", "今天的小練習:" + data.get("practice", ""), "",
                 "出處與依據:" + data.get("sources_text", ""), "",
                 "本片為佛法觀念的白話介紹,經典引用皆為意譯,不代表任何宗派或團體的立場。", ""]
        if block:
            lines += [block, ""]
        footage_lines = []
        for pick in read(self.dir / "footage.json"):
            it = pick["item"]
            line = f"{it.get('title', '')} — {it.get('author', '')} {it.get('webpage_url', '')}"
            if line not in footage_lines:
                footage_lines.append(line)
        src_file = vidpipe.work_dir(self.cfg, self.short_id) / "sources.json"
        music = (read(src_file).get("music") or {}).get("source") if src_file.is_file() else None
        if music:
            licence = music.get("license") or {}
            lic = licence.get("name") or licence.get("label", "")
            lines += [f"配樂:{music.get('title', '')} — {music.get('author', '')}({lic})"
                      f"{music.get('webpage_url', '')} [{music.get('asset_id', '')}]", ""]
        lines += ["畫面素材(YouTube;標示 CC BY 的已列作者,其餘為創作者聲明可免費使用或授權未標示):",
                  *footage_lines, "",
                  " ".join("#" + t.lstrip("#") for t in data.get("hashtags", []))]
        description = "\n".join(lines).strip() + "\n"
        write(self.dir / "description.txt", description)
        return {**data, "description": description}

    # ---- 9 qc
    def quality(self, script: dict, made: dict, description: str) -> dict:
        video = Path(made["path"])
        report = qc.check(self.cfg, video=video, fmt=self.fmt, script=script,
                          vp_dir=vidpipe.work_dir(self.cfg, self.short_id),
                          credits=made["credits"], description=description,
                          allow_unknown=self.cfg["visuals"].get("allow_unknown_license", False))
        write(self.dir / "qc.json", report)
        qc.contact_sheet(self.cfg, video, self.dir / "sheet.jpg",
                         every=max(1.0, report["duration"] / 60))
        self.log(f"品質檢查:{'通過' if report['ok'] else '不通過'} "
                 f"硬性失敗 {report['hard_fail']} 提醒 {report['soft_fail']}")
        return report

    # ---- 10 export
    def export(self, script: dict, made: dict, pkg: dict, report: dict) -> Path:
        dest = Path(self.cfg["out"]) / f"{self.date}_{self.topic['id']}_{self.fmt}"
        dest.mkdir(parents=True, exist_ok=True)
        video = Path(made["path"])
        shutil.copy2(video, dest / "video.mp4")
        for suffix, name in ((".cover.jpg", "cover.jpg"), (".credits.md", "credits.md")):
            src = video.with_name(video.stem + suffix)
            if src.is_file():
                shutil.copy2(src, dest / name)
        vp_dir = vidpipe.work_dir(self.cfg, self.short_id)
        if (vp_dir / "subs.srt").is_file():
            shutil.copy2(vp_dir / "subs.srt", dest / "subtitles.srt")
        for name in ("script.json", "description.txt", "qc.json", "sheet.jpg", "manifest.json"):
            if (self.dir / name).is_file():
                shutil.copy2(self.dir / name, dest / name)
        meta = {"run_id": self.id, "topic": self.topic["id"], "topic_name": self.topic["name"],
                "format": self.fmt, "duration": report["duration"], "title": script["title"],
                "titles": pkg.get("titles", []), "tags": pkg.get("tags", []),
                "hashtags": pkg.get("hashtags", []),
                "pinned_comment": pkg.get("pinned_comment", ""),
                "sources": script.get("sources", []), "practice": script.get("practice", ""),
                "review": script.get("review", {}), "qc_ok": report["ok"],
                "qc_soft_fail": report["soft_fail"], "loudness": report["loudness"],
                "voice_model": self.cfg["voice"].get("model", "")}
        write(dest / "metadata.json", meta)
        return dest

    def run(self) -> Path:
        brief = self.brief()
        script = self.script(brief)
        banned: set[str] = set()
        ban_file = self.dir / "banned.json"
        if ban_file.is_file():
            banned = set(read(ban_file))
        picks = self.visuals(script, banned or None)
        self.manifest(script, picks)
        made = self.make()
        pkg = self.package(script, made)
        report = self.quality(script, made, pkg["description"])
        if not report["ok"]:
            raise Rejected(f"品質檢查硬性失敗:{report['hard_fail']}(見 {self.dir / 'qc.json'})")
        dest = self.export(script, made, pkg, report)
        self.log(f"完成:{dest}")
        return dest


def load_topics(cfg: dict) -> list[dict]:
    return read(Path(cfg["root"]) / "topics" / "topics.json")["topics"]


def done_file(cfg: dict) -> Path:
    return Path(abspath(cfg, "topics/_done.json"))


def mark_done(cfg: dict, run: Run, dest: Path, duration: float) -> None:
    path = done_file(cfg)
    data = read(path) if path.is_file() else []
    data = [d for d in data if d.get("run_id") != run.id]
    data.append({"run_id": run.id, "topic": run.topic["id"], "format": run.fmt,
                 "duration": round(duration, 1), "out": str(dest.relative_to(cfg["root"]))
                 if str(dest).startswith(cfg["root"]) else str(dest)})
    write(path, data)
