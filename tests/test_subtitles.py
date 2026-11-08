from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from mediagen import bright_rows, font_name, has_cjk_font, make
from tlhelp import clips_for, parse
from ttsfake import FakeTTS

from vidpipe import cli, subtitles, timeline
from vidpipe.errors import ConfigError
from vidpipe.video import OutputSpec

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")

PORTRAIT = OutputSpec(width=1080, height=1920)
ZH_EM = subtitles.layout_for(PORTRAIT).zh_em


def build_for(scenes, durations, *, spec=PORTRAIT, font="Noto Sans CJK TC", **top):
    m = parse(scenes, **top)
    tl = timeline.build(m, clips_for(m, durations))
    return tl, subtitles.build(tl, m.subtitles, spec, font)


# ------------------------------------------------------------------ 版面


def test_portrait_layout_has_a_shorts_safe_bottom_margin_and_about_14_chars_per_line():
    lay = subtitles.layout_for(PORTRAIT)
    assert (lay.font_size, lay.margin_bottom, lay.margin_side) == (65, 384, 76)
    assert 14 <= lay.zh_em < 15 and lay.en_size < lay.font_size


def test_landscape_scales_by_the_short_side_and_keeps_a_small_bottom_margin():
    lay = subtitles.layout_for(OutputSpec(width=1920, height=1080))
    assert lay.font_size == 65 and lay.margin_bottom == round(1080 * 0.08)
    assert lay.zh_em > 24  # 橫式一行放得下很多字


# ------------------------------------------------------------------ 折行


def lines_ok(lines, max_em=ZH_EM):
    for line in lines:
        assert subtitles._em(line) <= max_em + 1e-6, line
        assert line[0] not in subtitles._NO_START, line
        assert line[-1] not in subtitles._NO_END, line


def test_short_text_is_one_line_and_empty_text_is_nothing():
    assert subtitles.wrap("你好", ZH_EM) == ["你好"]
    assert subtitles.wrap("台北的夜晚，從一碗熱湯開始。", ZH_EM) == ["台北的夜晚，從一碗熱湯開始。"]
    assert subtitles.wrap("   ", ZH_EM) == []


def test_two_lines_are_balanced_not_fourteen_plus_one():
    lines = subtitles.wrap("今天我們去吃牛肉麵加上滷味再喝一杯冬瓜茶好不好", ZH_EM)
    assert len(lines) == 2 and abs(len(lines[0]) - len(lines[1])) <= 2
    lines_ok(lines)


def test_breaks_after_a_comma_rather_than_in_the_middle_of_a_phrase():
    assert subtitles.wrap("台北的夜晚，從一碗熱湯開始，一路吃到天亮。", ZH_EM) == [
        "台北的夜晚，從一碗熱湯開始，", "一路吃到天亮。"]


def test_punctuation_never_starts_a_line_and_opening_quotes_never_end_one():
    text = "他說：「走吧，我們去夜市吃好吃的東西，」然後就出發了。接著又說：「等等我！」"
    for page in subtitles.paginate(text, ZH_EM):
        lines_ok(page)
    # 逐個長度試：不管在哪裡剛好滿行，都不會讓標點跑到行首
    for n in range(10, 40):
        lines_ok(subtitles.wrap("字" * n + "，。！？」" + "字" * 5, ZH_EM))


def test_latin_words_and_numbers_are_not_split_in_the_middle():
    for line in subtitles.wrap("iPhone 15 Pro Max 的價格是 3.5 萬元，比去年便宜了不少。", ZH_EM):
        for word in ("iPhone", "Pro", "Max", "3.5"):
            assert word in line or not any(w.startswith(word[:2]) for w in line.split()), line
    joined = " ".join(subtitles.wrap("iPhone 15 Pro Max 的價格是 3.5 萬元", ZH_EM))
    assert "3.5" in joined and "iPhone" in joined
    english = subtitles.wrap("The night market is lively and full of the smell of noodles.", 23.2)
    assert len(english) == 2 and " ".join(english).replace("  ", " ").startswith("The night")
    assert all(not line.startswith(" ") and not line.endswith(" ") for line in english)


def test_a_word_longer_than_a_line_still_terminates():
    out = subtitles.wrap("x" * 80 + " 好", 10.0)
    assert out and "".join(out).replace(" ", "").count("x") == 80


def test_long_sentence_is_paged_into_at_most_two_lines_per_page():
    text = "夜市很熱鬧，人潮不斷湧入，每個攤位都排著長長的隊伍，空氣裡都是炒米粉的香味。"
    pages = subtitles.paginate(text, ZH_EM)
    assert len(pages) == 2 and all(1 <= len(p) <= 2 for p in pages)
    assert pages[0] == ["夜市很熱鬧，人潮不斷湧入，"]            # 在逗號切頁，不是硬切在 19 字
    assert "".join("".join(p) for p in pages) == text            # 不掉字
    for page in pages:
        lines_ok(page)


# ------------------------------------------------------------------ 時間


def test_a_single_sentence_shows_for_its_speech_plus_a_short_hold():
    _, subs = build_for([{"id": "a", "text": "你好，世界。"}], [1.0])
    (e,) = subs.entries
    assert (e.start, e.end) == (0.15, 1.3)            # 聲音 0.15–1.15，多停 0.15
    assert e.zh == ("你好，世界。",) and e.en == ()


def test_hold_never_runs_into_the_next_sentence_and_entries_never_overlap():
    tl, subs = build_for([{"id": "a", "text": "一。二。三。"}], [0.5, 0.5, 0.5],
                         timing={"lead": 0, "gap": 0.05, "tail": 0})
    starts = [c.start for c in tl.cues]
    assert [e.start for e in subs.entries] == starts
    for cur, nxt in zip(subs.entries, subs.entries[1:], strict=False):
        assert cur.end <= nxt.start + 1e-9
    assert subs.entries[0].end == starts[1]           # gap 只有 0.05，所以停留被下一句截斷
    assert subs.entries[-1].end <= tl.total


def test_very_short_sentences_stay_up_for_a_minimum_time():
    _, subs = build_for([{"id": "a", "text": "好。"}, {"id": "b", "min_duration": 3}], [0.1])
    e = subs.entries[0]
    assert e.end - e.start == pytest.approx(0.4, abs=0.001)


def test_pages_share_the_sentence_time_by_character_count_and_touch_each_other():
    text = "夜市很熱鬧，人潮不斷湧入，每個攤位都排著長長的隊伍，空氣裡都是炒米粉的香味。"
    tl, subs = build_for([{"id": "a", "text": text}], [6.0])
    a, b = subs.entries
    assert a.start == tl.cues[0].start and a.end == b.start and b.end >= tl.cues[0].end
    # 第一頁 14 字、第二頁 24 字：時間比例約 14:24
    assert (a.end - a.start) / (b.end - a.start) == pytest.approx(14 / 38, abs=0.03)


# ------------------------------------------------------------------ 雙語


def test_bilingual_puts_english_under_the_chinese_and_splits_it_with_the_pages():
    scenes = [{"id": "a", "sentences": [
        {"text": "夜市很熱鬧，人潮不斷湧入，每個攤位都排著長長的隊伍，空氣裡都是炒米粉的香味。",
         "en": "The night market is lively, crowds keep pouring in, every stall has a long "
               "queue, and the air smells of fried noodles."}]}]
    _, subs = build_for(scenes, [6.0], subtitles={"langs": "zh-TW+en"})
    a, b = subs.entries
    assert a.en and b.en
    assert " ".join([*a.en, *b.en]).split() == scenes[0]["sentences"][0]["en"].split()  # 不掉字
    assert a.en[0].startswith("The night market")
    srt_first = subs.srt.split("\n\n")[0].splitlines()
    assert srt_first[2:2 + len(a.zh)] == list(a.zh) and srt_first[-1] == a.en[-1]


def test_missing_english_falls_back_to_chinese_only_with_a_warning():
    scenes = [{"id": "a", "sentences": [{"text": "有英文。", "en": "With English."},
                                        {"text": "沒英文。"}]},
              {"id": "b", "sentences": [{"text": "也沒有。"}]}]
    _, subs = build_for(scenes, [1, 1, 1], subtitles={"langs": "zh-TW+en"})
    assert [e.en for e in subs.entries] == [("With English.",), (), ()]
    assert len(subs.warnings) == 1 and "2 句沒有 en" in subs.warnings[0]
    assert "a, b" in subs.warnings[0]


def test_chinese_only_mode_ignores_english_and_does_not_warn():
    scenes = [{"id": "a", "sentences": [{"text": "你好。", "en": "Hello."}]}]
    _, subs = build_for(scenes, [1.0], subtitles={"langs": "zh-TW"})
    assert subs.entries[0].en == () and subs.warnings == () and "Hello" not in subs.srt


# ------------------------------------------------------------------ 格式


def test_srt_format():
    _, subs = build_for([{"id": "a", "text": "你好，世界。"}, {"id": "b", "text": "再見。"}],
                        [1.0, 0.5])
    assert subs.srt == (
        "1\n00:00:00,150 --> 00:00:01,300\n你好，世界。\n\n"
        "2\n00:00:01,750 --> 00:00:02,400\n再見。\n")


def test_srt_time_rolls_over_hours():
    assert subtitles._srt_time(3661.0075) == "01:01:01,008"
    assert subtitles._ass_time(3661.0075) == "1:01:01.01"


def test_ass_header_styles_and_dialogue():
    _, subs = build_for([{"id": "a", "text": "你好，世界。"}], [1.0], font="My Font")
    ass = subs.ass
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass and "WrapStyle: 2" in ass
    default = next(line for line in ass.splitlines() if line.startswith("Style: Default"))
    fields = default.removeprefix("Style: ").split(",")
    # Name, Font, Size, …, Bold(-1), …, BorderStyle(1), Outline, Shadow, Alignment(2), L, R, V
    assert fields[:3] == ["Default", "My Font", "65"] and fields[7] == "-1"
    assert fields[15:] == ["1", "5", "1", "2", "76", "76", "384", "1"]
    assert "Style: En,My Font,40," in ass
    assert "Dialogue: 0,0:00:00.15,0:00:01.30,Default,,0,0,0,,你好，世界。" in ass
    assert ass.startswith("[Script Info]") and ass.endswith("\n")


def test_ass_marks_line_breaks_switches_style_for_english_and_escapes_braces():
    scenes = [{"id": "a", "sentences": [
        {"text": "今天我們去吃牛肉麵加上滷味再喝一杯冬瓜茶好不好{笑}",
         "en": "Beef noodles {lol}"}]}]
    _, subs = build_for(scenes, [3.0], subtitles={"langs": "zh-TW+en"})
    text = next(x for x in subs.ass.splitlines() if x.startswith("Dialogue")).split(",,")[-1]
    assert "\\N" in text and "\\N{\\rEn}Beef noodles" in text
    assert "{笑}" not in text and "｛笑｝" in text and "｛lol｝" in text


def test_no_sentences_means_no_entries():
    _, subs = build_for([{"id": "a", "min_duration": 2}], [])
    assert subs.entries == () and subs.srt == ""


def test_subtitle_font_config_validation(monkeypatch):
    from vidpipe import config as config_mod

    assert config_mod.load().subtitle_font == "Noto Sans CJK TC"
    monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", "PingFang TC")
    assert config_mod.load().subtitle_font == "PingFang TC"
    for bad in ("", "A,B", "  "):
        monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", bad)
        with pytest.raises(ConfigError):
            config_mod.load()


# ------------------------------------------------------------------ narrate 一併產生


MANIFEST = {
    "schema": "vidpipe.manifest/1", "id": "subs-01",
    "subtitles": {"langs": "zh-TW+en"},
    "scenes": [{"id": "s1", "sentences": [{"text": "你好。", "en": "Hello."},
                                          {"text": "晚安。"}]},
               {"id": "s2", "min_duration": 1.0}],
}


def _run_narrate(tmp_path, fake_server, monkeypatch, capsys, data=MANIFEST):
    FakeTTS(fake_server, speech=lambda text: 0.1 * len(text))
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", fake_server.url)
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "work"))
    path = tmp_path / "shorts.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    code = cli.main(["narrate", str(path)])
    out = json.loads(capsys.readouterr().out)
    return code, out, tmp_path / "work" / "shorts" / data["id"]


@needs_ffmpeg
def test_narrate_writes_srt_and_ass_and_records_them_in_the_timeline(
        tmp_path, fake_server, monkeypatch, capsys):
    code, out, root = _run_narrate(tmp_path, fake_server, monkeypatch, capsys)
    assert code == 0
    assert (root / "subs.srt").read_text(encoding="utf-8").startswith("1\n00:00:00,150")
    assert "Dialogue:" in (root / "subs.ass").read_text(encoding="utf-8")
    tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
    assert tl["subtitles"] == {"langs": "zh-TW+en", "style": "bold-bottom", "burn_in": True,
                               "font": "Noto Sans CJK TC", "srt": "subs.srt", "ass": "subs.ass",
                               "entries": 2}
    result = out["output"]
    assert result["subtitles"]["entries"] == 2 and result["subtitles"]["srt"].endswith("subs.srt")
    assert result["warnings"] == tl["warnings"] and "沒有 en" in result["warnings"][0]
    assert sorted(p.name for p in root.iterdir()) == [
        "manifest.json", "narration.wav", "subs.ass", "subs.srt", "timeline.json"]


@needs_ffmpeg
def test_a_manifest_without_narration_removes_stale_subtitles(
        tmp_path, fake_server, monkeypatch, capsys):
    silent = {"schema": "vidpipe.manifest/1", "id": "subs-01",
              "scenes": [{"id": "a", "min_duration": 1.5}]}
    root = tmp_path / "work" / "shorts" / "subs-01"
    root.mkdir(parents=True)
    for name in ("subs.srt", "subs.ass", "narration.wav"):
        (root / name).write_text("stale", encoding="utf-8")
    code, out, _ = _run_narrate(tmp_path, fake_server, monkeypatch, capsys, silent)
    assert code == 0 and out["output"]["subtitles"] is None
    assert sorted(p.name for p in root.iterdir()) == ["manifest.json", "timeline.json"]
    assert json.loads((root / "timeline.json").read_text(encoding="utf-8"))["subtitles"] is None


@needs_ffmpeg
def test_subtitle_files_are_identical_on_rerun_and_follow_the_manifest_font(
        tmp_path, fake_server, monkeypatch, capsys):
    monkeypatch.setenv("VIDPIPE_SUBTITLE_FONT", "My Font")
    _, _, root = _run_narrate(tmp_path, fake_server, monkeypatch, capsys)
    first = {n: (root / n).read_bytes() for n in ("subs.srt", "subs.ass", "timeline.json")}
    assert b"My Font" in first["subs.ass"]
    _run_narrate(tmp_path, fake_server, monkeypatch, capsys)
    assert {n: (root / n).read_bytes() for n in first} == first


# ------------------------------------------------------------------ 真的燒進畫面（要繁中字體）


@needs_ffmpeg
@pytest.mark.skipif(not has_cjk_font(), reason="需要 libass 與一個繁中字體")
def test_burned_in_subtitles_land_in_the_bottom_area_clear_of_the_shorts_overlay(tmp_path):
    spec = OutputSpec(width=540, height=960)  # 縮小一半讓測試快；版面按比例
    tl, subs = build_for([{"id": "a", "text": "台北的夜晚，從一碗熱湯開始，一路吃到天亮。"}],
                         [2.0], spec=spec, font=font_name())
    ass = tmp_path / "subs.ass"
    ass.write_text(subs.ass, encoding="utf-8")
    src = make(tmp_path / "bg.mp4", "color=c=0x202020:s=540x960", audio=False, seconds=3)
    out = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", f"ass={ass}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    rows = bright_rows(out, 1.0, 540, 960)
    assert rows, "字幕沒有出現在畫面上"
    lay = subtitles.layout_for(spec)
    assert max(rows) < 960 - lay.margin_bottom + 5      # 最底的字離底邊至少 20%
    assert min(rows) > 960 * 0.5                         # 在畫面下半部，不是亂跑
    assert not bright_rows(out, 0.02, 540, 960)          # 聲音還沒開始時沒有字
    assert re.search(r"-->", subs.srt)
