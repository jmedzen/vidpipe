"""單元測試與端對端冒煙測試(不連網路:假的 vidpipe、假的素材池、預先寫好的 LLM 回應)。

跑法:python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import cli, config, footage, llm, stages, textcheck  # noqa: E402

THEMES = set(json.loads((ROOT / "assets" / "footage.json").read_text(encoding="utf-8"))["themes"])
FIXTURES = json.loads((Path(__file__).parent / "fixtures.json").read_text(encoding="utf-8"))
SCRIPT, REVIEW, PACKAGE = FIXTURES["script"], FIXTURES["review"], FIXTURES["package"]
LONG_SCENE = FIXTURES["long_scene"]


class TextCheck(unittest.TestCase):
    def test_normalize_turns_ascii_punctuation_fullwidth(self):
        self.assertEqual(textcheck.normalize("為什麼,越想放下?"), "為什麼，越想放下？")

    def test_split_beats_cuts_at_sentence_ends(self):
        beats = textcheck.split_beats(LONG_SCENE)
        self.assertEqual(len(beats), 2)
        self.assertEqual("".join(beats), LONG_SCENE)

    def test_lint_passes_a_good_script_and_flags_bad_ones(self):
        good = textcheck.normalize_script(json.loads(json.dumps(SCRIPT)))
        self.assertTrue(textcheck.lint(good, min_chars=80, max_chars=140, themes=THEMES)["ok"])
        for change, needle in (
                ({"sources": []}, "出處"),
                ({"title": ""}, "標題"),
                ({"scenes": [{"visual": "sky", "text": "大家好，今天我們來聊聊放下。"}]}, "寒暄"),
                ({"scenes": [{"visual": "nope", "text": "為什麼放不下？"}]}, "visual"),
                ({"scenes": [{"visual": "sky", "text": "这个问题很难说。"}]}, "簡體")):
            bad = {**good, **change}
            report = textcheck.lint(bad, min_chars=1, max_chars=999, themes=THEMES)
            self.assertFalse(report["ok"], change)
            self.assertTrue(any(needle in i["problem"] for i in report["issues"]), (needle, report))
        self.assertFalse(textcheck.lint(good, min_chars=300, max_chars=400, themes=THEMES)["ok"])

    def test_traditional_text_is_not_mistaken_for_simplified(self):
        text = "".join(s["text"] for s in SCRIPT["scenes"])
        text += "這個時間還沒過，對開關門問題說話，學習體驗發現關係"
        self.assertEqual(textcheck.simplified_found(text), [])


class Footage(unittest.TestCase):
    def test_assign_prefers_clear_licences_and_avoids_back_to_back_repeats(self):
        def item(i, kind, dur=300):
            return {"id": i, "duration": dur, "license": {"kind": kind}, "title": i}
        pools = {"sky": [item("y:by1", "free-attribution"), item("y:by2", "free-attribution"),
                         item("y:unk", "unknown")],
                 "sand": [item("y:only", "unknown", 80)]}
        scenes = [{"id": f"s{i}", "visual": v, "seconds": 6}
                  for i, v in enumerate(["sky", "sky", "sky", "sand", "sand"])]
        picks = footage.assign(scenes, pools, "run")
        ids = [p["asset_id"] for p in picks]
        self.assertNotIn("y:unk", ids)
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(ids[3:], ["y:only", "y:only"])
        self.assertGreaterEqual(abs(picks[3]["start"] - picks[4]["start"]), 4)
        for p in picks:
            self.assertLess(p["start"] + 6, p["item"]["duration"])
        self.assertEqual(picks, footage.assign(scenes, pools, "run"))       # 可重現
        with self.assertRaises(footage.FootageError):
            footage.assign(scenes, pools, "run", banned={"y:only"})

    def test_usable_filters_titles_and_licences(self):
        ok = {"title": "Ocean waves", "duration": 120, "license": {"kind": "free-attribution"}}
        self.assertTrue(footage.usable(ok, ["podcast"], False))
        self.assertFalse(footage.usable({**ok, "title": "Sleep Podcast"}, ["podcast"], False))
        self.assertFalse(footage.usable({**ok, "license": {"kind": "unknown"}}, [], False))
        self.assertTrue(footage.usable({**ok, "license": {"kind": "unknown"}}, [], True))
        self.assertFalse(footage.usable({**ok, "duration": 10}, [], True))


class LLMFiles(unittest.TestCase):
    def test_files_backend_waits_for_a_response_file(self):
        os.environ["PIPELINE_LLM"] = "files"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(llm.NeedsResponse):
                llm.ask(Path(d), "x", "prompt")
            self.assertEqual((Path(d) / "x.prompt.md").read_text(encoding="utf-8"), "prompt")
            (Path(d) / "x.response.json").write_text('```json\n{"a": 1}\n```', encoding="utf-8")
            self.assertEqual(llm.ask(Path(d), "x", "prompt"), {"a": 1})


@unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ.update({"PIPELINE_LLM": "files", "PIPELINE_WORK": str(self.tmp / "work"),
                           "PIPELINE_OUT": str(self.tmp / "out"), "FAKE_SCENE_SECONDS": "7"})
        self.cfg = config.load()
        self.cfg["vidpipe"] = {"bin": str(ROOT / "tests" / "fake_vidpipe.py"), "timeout": 300,
                               "env": {"VIDPIPE_VIDGRAB_URL": "http://127.0.0.1:1"}}
        self.cfg["music"] = None
        self._pool = footage.pool
        footage.pool = lambda vg, lib, theme, cache, **kw: [
            {"id": "youtube:a1", "title": "Clip", "author": "Alice", "duration": 300,
             "license": {"kind": "free-attribution"}, "webpage_url": "https://example.com/a1"}]
        self._done = stages.done_file
        stages.done_file = lambda cfg: self.tmp / "done.json"

    def tearDown(self):
        footage.pool, stages.done_file = self._pool, self._done
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key in ("PIPELINE_WORK", "PIPELINE_OUT", "FAKE_SCENE_SECONDS"):
            os.environ.pop(key, None)

    def test_topic_to_finished_package_resuming_at_each_llm_step(self):
        topic = {"id": "letting-go", "name": "放下", "concept": "c", "sources": ["四聖諦"]}
        run_dir = self.tmp / "work" / "20260101_letting-go_short"
        answers = [("script.r1", SCRIPT), ("review.r1", REVIEW), ("package", PACKAGE)]
        for name, answer in answers:      # 每次停在缺回應的那一步;補上回應後重跑就接著做
            status, detail = cli._one(self.cfg, topic, "short", 25, "20260101")
            self.assertEqual(status, "waiting", detail)
            self.assertTrue((run_dir / f"{name}.prompt.md").is_file())
            (run_dir / f"{name}.response.json").write_text(json.dumps(answer, ensure_ascii=False),
                                                           encoding="utf-8")
        status, detail = cli._one(self.cfg, topic, "short", 25, "20260101")
        self.assertEqual(status, "done", detail)
        dest = Path(detail)
        for name in ("video.mp4", "cover.jpg", "credits.md", "description.txt", "metadata.json",
                     "script.json", "subtitles.srt", "qc.json", "manifest.json", "sheet.jpg"):
            self.assertTrue((dest / name).is_file(), name)
        meta = json.loads((dest / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["qc_ok"])
        self.assertTrue(30 <= meta["duration"] <= 120)
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["overlays"][0]["text"], "為什麼越想放下，反而越放不下？")
        # 標點轉成全形、場景切成鏡頭
        self.assertEqual(manifest["scenes"][0]["text"], "為什麼越想放下，反而越放不下？")
        self.assertGreater(len(manifest["scenes"]), len(SCRIPT["scenes"]))
        self.assertIn("example.com/a1", (dest / "description.txt").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((self.tmp / "done.json").read_text())[0]["topic"], "letting-go")

    def test_a_too_short_video_is_rejected_by_quality_checks(self):
        os.environ["FAKE_SCENE_SECONDS"] = "1"
        topic = {"id": "letting-go", "name": "放下", "concept": "c", "sources": ["四聖諦"]}
        run_dir = self.tmp / "work" / "20260101_letting-go_short"
        run_dir.mkdir(parents=True)
        for name, answer in (("script.r1", SCRIPT), ("review.r1", REVIEW), ("package", PACKAGE)):
            (run_dir / f"{name}.response.json").write_text(json.dumps(answer, ensure_ascii=False),
                                                           encoding="utf-8")
        status, detail = cli._one(self.cfg, topic, "short", 25, "20260101")
        self.assertEqual(status, "rejected")
        self.assertIn("duration", detail)

    def test_a_failed_review_sends_the_script_back_with_feedback(self):
        topic = {"id": "letting-go", "name": "放下", "concept": "c", "sources": ["四聖諦"]}
        run_dir = self.tmp / "work" / "20260101_letting-go_short"
        run_dir.mkdir(parents=True)
        bad = {**REVIEW, "pass": False, "issues": [
            {"severity": "hard", "where": "s2", "problem": "把意譯寫成佛說", "fix": "加上意譯"}]}
        for name, answer in (("script.r1", SCRIPT), ("review.r1", bad)):
            (run_dir / f"{name}.response.json").write_text(
                json.dumps(answer, ensure_ascii=False), encoding="utf-8")
        status, _ = cli._one(self.cfg, topic, "short", 25, "20260101")
        self.assertEqual(status, "waiting")
        prompt = (run_dir / "script.r2.prompt.md").read_text(encoding="utf-8")
        self.assertIn("把意譯寫成佛說", prompt)
        self.assertFalse((run_dir / "script.json").exists())


if __name__ == "__main__":
    unittest.main()
