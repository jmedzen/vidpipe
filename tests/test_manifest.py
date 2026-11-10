from __future__ import annotations

import copy
import json

import pytest

from vidpipe import manifest
from vidpipe.config import Config
from vidpipe.errors import BadInput, NotFound

BASE = {
    "schema": "vidpipe.manifest/1",
    "id": "taipei-night-01",
    "scenes": [{"id": "s1", "text": "台北的夜晚，從一碗熱湯開始。"}],
}


def doc(**changes):
    data = copy.deepcopy(BASE)
    data.update(changes)
    return data


def fails(data, *needles, **kw):
    with pytest.raises(BadInput) as err:
        manifest.parse(data, **kw)
    text = err.value.message + err.value.hint
    for needle in needles:
        assert needle in text, (needle, text)
    return err.value


def test_minimal_manifest_gets_shorts_defaults():
    m = manifest.parse(BASE)
    assert m.id == "taipei-night-01" and m.title == ""
    assert (m.output.width, m.output.height, m.output.fps) == (1080, 1920, 30)
    assert m.aspect == "9:16"
    assert (m.timing.lead, m.timing.gap, m.timing.tail) == (0.15, 0.25, 0.45)
    assert (m.subtitles.langs, m.subtitles.burn_in, m.subtitles.style) == \
        ("zh-TW", True, "bold-bottom")
    assert m.voice == {} and m.music is None
    scene = m.scenes[0]
    assert (scene.fit, scene.focus, scene.mute_source, scene.clip) == \
        ("blur_pad", (0.5, 0.5), True, None)


def test_scene_text_is_split_into_sentences():
    m = manifest.parse(doc(scenes=[{"id": "s1", "text": "第一句。第二句！第三句？"}]))
    assert [s.text for s in m.scenes[0].sentences] == ["第一句。", "第二句！", "第三句？"]
    assert all(s.en == "" for s in m.scenes[0].sentences)


def test_explicit_sentences_keep_their_english_pairing():
    scene = {"id": "s2", "sentences": [{"text": "夜市很熱鬧。", "en": "The market is lively."},
                                       {"text": "不用英文。"}]}
    m = manifest.parse(doc(scenes=[scene]))
    assert [(s.text, s.en) for s in m.scenes[0].sentences] == [
        ("夜市很熱鬧。", "The market is lively."), ("不用英文。", "")]


def test_scene_without_narration_needs_min_duration():
    fails(doc(scenes=[{"id": "s3"}]), "scenes[0]", "min_duration")
    m = manifest.parse(doc(scenes=[{"id": "s3", "min_duration": 2.5}]))
    assert m.scenes[0].sentences == () and m.scenes[0].min_duration == 2.5


def test_text_and_sentences_are_mutually_exclusive():
    scene = {"id": "s1", "text": "你好。", "sentences": [{"text": "你好。"}]}
    fails(doc(scenes=[scene]), "scenes[0]", "只能給一個")


def test_punctuation_only_text_cannot_be_spoken():
    fails(doc(scenes=[{"id": "s1", "text": "……！！"}]), "scenes[0].text", "沒有可以唸的字")
    scene = {"id": "s1", "sentences": [{"text": "好。"}, {"text": "。。"}]}
    fails(doc(scenes=[scene]), "scenes[0].sentences[1].text")


def test_unknown_fields_are_reported_with_their_path_and_the_allowed_ones():
    scenes = [{"id": "a", "text": "好。"}, {"id": "b", "txt": "好。", "text": "好。"}]
    err = fails(doc(scenes=scenes), "scenes[1]", "txt")
    assert "text" in err.hint and "clip" in err.hint
    fails(doc(colour="red"), "（最上層）", "colour")
    fails(doc(output={"aspect": "9:16", "fp": 30}), "output", "fp")
    fails(doc(timing={"lead": 0.1, "pause": 1}), "timing", "pause")


def test_top_level_requirements():
    fails({k: v for k, v in BASE.items() if k != "scenes"}, "scenes")
    fails(doc(schema="vidpipe.manifest/2"), "schema")
    fails(doc(scenes=[]), "scenes", "非空")
    fails(doc(scenes="nope"), "scenes")
    fails([], "（最上層）", "物件")


def test_ids_are_folder_safe_and_scene_ids_unique():
    for bad in ("Has Space", "../x", "UPPER", "", "-lead", "x" * 65):
        fails(doc(id=bad), "id")
    fails(doc(scenes=[{"id": "../s", "text": "好。"}]), "scenes[0].id")
    fails(doc(scenes=[{"id": "a", "text": "好。"}, {"id": "a", "text": "好。"}]),
          "scenes[1].id", "重複")


def test_clip_needs_exactly_one_source_and_a_sane_start():
    ok = manifest.parse(doc(scenes=[{"id": "s", "text": "好。",
                                     "clip": {"asset_id": "pixabay:123", "start": 3.0}}]))
    assert ok.scenes[0].clip == {"asset_id": "pixabay:123", "start": 3.0}
    path_clip = manifest.parse(doc(scenes=[{"id": "s", "text": "好。",
                                            "clip": {"path": "/a/b.mp4"}}]))
    assert path_clip.scenes[0].clip == {"path": "/a/b.mp4"}
    for clip in ({}, {"asset_id": "a", "path": "b"}, {"asset_id": "a", "start": -1},
                 {"asset_id": "a", "start": True}, {"asset_id": 5}, {"asset_id": "a", "x": 1}):
        fails(doc(scenes=[{"id": "s", "text": "好。", "clip": clip}]), "scenes[0].clip")


def test_fit_focus_and_mute_source_are_validated_with_paths():
    scene = {"id": "s", "text": "好。", "fit": "crop", "focus": "0.3,0.4", "mute_source": False}
    s = manifest.parse(doc(scenes=[scene])).scenes[0]
    assert (s.fit, s.focus, s.mute_source) == ("crop", (0.3, 0.4), False)
    fails(doc(scenes=[{"id": "s", "text": "好。", "fit": "stretch"}]), "scenes[0].fit", "blur_pad")
    fails(doc(scenes=[{"id": "s", "text": "好。", "focus": "left"}]), "scenes[0].focus")
    fails(doc(scenes=[{"id": "s", "text": "好。", "focus": "1.5,0"}]), "scenes[0].focus")
    fails(doc(scenes=[{"id": "s", "text": "好。", "mute_source": "no"}]),
          "scenes[0].mute_source")


def test_output_aspect_and_fps():
    m = manifest.parse(doc(output={"aspect": "16:9", "fps": 24}))
    assert (m.output.width, m.output.height, m.output.fps, m.aspect) == (1920, 1080, 24, "16:9")
    fails(doc(output={"aspect": "4:3"}), "output.aspect", "9:16")
    fails(doc(output={"fps": 0}), "output.fps")
    fails(doc(output={"fps": True}), "output.fps")


def test_timing_overrides_and_range():
    m = manifest.parse(doc(timing={"lead": 0, "gap": 0.5, "tail": 1}))
    assert (m.timing.lead, m.timing.gap, m.timing.tail) == (0, 0.5, 1)
    fails(doc(timing={"gap": -1}), "timing.gap")
    fails(doc(timing={"tail": "long"}), "timing.tail")


def test_subtitle_defaults_come_from_config_and_manifest_overrides_them():
    m = manifest.parse(BASE, subtitle_langs="zh-TW+en", burn_in=False)
    assert (m.subtitles.langs, m.subtitles.burn_in) == ("zh-TW+en", False)
    m = manifest.parse(doc(subtitles={"langs": "zh-TW", "burn_in": True}),
                       subtitle_langs="zh-TW+en", burn_in=False)
    assert (m.subtitles.langs, m.subtitles.burn_in) == ("zh-TW", True)
    fails(doc(subtitles={"langs": "en"}), "subtitles.langs", "zh-TW+en")
    fails(doc(subtitles={"style": "fancy"}), "subtitles.style")
    fails(doc(subtitles={"burn_in": "yes"}), "subtitles.burn_in")


def test_cover_takes_an_optional_time_and_title():
    assert manifest.parse(doc()).cover is None
    assert manifest.parse(doc(cover={"at": 2.5, "text": "標題"})).cover == {
        "at": 2.5, "text": "標題"}
    assert manifest.parse(doc(cover={"text": ""})).cover == {"text": ""}   # 空字串 = 不放字
    fails(doc(cover={"at": -1}), "cover.at")
    fails(doc(cover={"text": 3}), "cover.text")
    fails(doc(cover={"title": "x"}), "cover")


def test_music_needs_one_source_and_valid_values():
    m = manifest.parse(doc(music={"asset_id": "pixabay:9", "volume": 0.12,
                                  "duck_under_voice": True}))
    assert m.music == {"asset_id": "pixabay:9", "volume": 0.12, "duck_under_voice": True}
    assert manifest.parse(doc(music=None)).music is None
    fails(doc(music={"volume": 0.1}), "music", "二選一")
    fails(doc(music={"path": "a", "asset_id": "b"}), "music")
    fails(doc(music={"path": "a", "volume": 9}), "music.volume")
    fails(doc(music={"path": "a", "duck_under_voice": "yes"}), "music.duck_under_voice")
    assert manifest.parse(doc(music={"path": "a", "start": 12.5})).music == {
        "path": "a", "start": 12.5}
    fails(doc(music={"path": "a", "start": -1}), "music.start")
    fails(doc(music={"path": "a", "start": "0:12"}), "music.start")


def test_voice_in_manifest_overrides_config_even_when_empty():
    cfg = Config(tts_model="base-model", voice_ref="/v/a.wav", reference_text="甲")
    assert manifest.apply_voice(manifest.parse(BASE), cfg) == cfg
    m = manifest.parse(doc(voice={"model": "other", "voice_ref": ""}))
    out = manifest.apply_voice(m, cfg)
    assert (out.tts_model, out.voice_ref, out.reference_text) == ("other", "", "甲")
    assert cfg.tts_model == "base-model"  # 不改動原本的設定
    fails(doc(voice={"speed": 1}), "voice", "speed")


def test_load_reports_missing_file_and_bad_json(tmp_path):
    with pytest.raises(NotFound):
        manifest.load(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(BadInput, match="JSON"):
        manifest.load(bad)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(BASE, ensure_ascii=False), encoding="utf-8")
    assert manifest.load(good).id == "taipei-night-01"
    assert manifest.read_json(good) == BASE


def test_local_files_can_declare_a_licence():
    m = manifest.parse(doc(
        scenes=[{"id": "s1", "text": "夜市很熱鬧。",
                 "clip": {"path": "/a/bg.mp4", "license": "own"}}],
        music={"path": "/a/song.mp3", "license": "free-attribution",
               "credit": "Bob（CC BY 4.0）— https://x/song"}))
    assert m.scenes[0].clip == {"path": "/a/bg.mp4", "license": "own"}
    assert m.music["license"] == "free-attribution" and m.music["credit"].startswith("Bob")


def test_licence_declarations_are_checked():
    scene = {"id": "s1", "text": "夜市很熱鬧。"}
    fails(doc(scenes=[{**scene, "clip": {"asset_id": "pixabay:1", "license": "own"}}]),
          "scenes[0].clip.license", "只有本機檔案")
    fails(doc(scenes=[{**scene, "clip": {"path": "/a.mp4", "license": "mine"}}]),
          "scenes[0].clip.license")
    fails(doc(scenes=[{**scene, "clip": {"path": "/a.mp4", "credit": "Bob"}}]),
          "scenes[0].clip.credit", "license")
    fails(doc(music={"path": "/a.mp3", "license": "free-attribution"}), "music.license", "credit")


def test_overlays_get_defaults_and_are_validated():
    m = manifest.parse(doc(overlays=[{"text": "為什麼？"}, {"text": "系列", "position": "bottom",
                                                         "size": "small", "color": "yellow"}]))
    assert m.overlays[0] == {"text": "為什麼？", "position": "top", "size": "medium",
                             "color": "white"}
    assert m.overlays[1]["position"] == "bottom"
    assert manifest.parse(doc()).overlays == ()
    fails(doc(overlays=[{"text": "x", "where": "top"}]), "overlays[0]", "where")
    fails(doc(overlays=[{"text": "x", "position": "middle"}]), "overlays[0].position")
    fails(doc(overlays=[{"text": "字" * 41}]), "overlays[0].text", "40")
    fails(doc(overlays=[{"text": "x"}] * 5), "overlays", "最多 4 個")
    fails(doc(overlays={"text": "x"}), "overlays")


def test_subtitle_size_is_validated_and_defaults_to_normal():
    assert manifest.parse(doc()).subtitles.size == "normal"
    assert manifest.parse(doc(subtitles={"size": "xlarge"})).subtitles.size == "xlarge"
    fails(doc(subtitles={"size": "huge"}), "subtitles.size")
    assert manifest.parse(doc(overlays=[{"text": "x", "size": "xlarge"}])).overlays[0]["size"] \
        == "xlarge"
