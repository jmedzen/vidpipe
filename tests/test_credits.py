"""credits.md：素材來源、授權與說明欄範本。"""

from __future__ import annotations

from vidpipe import credits


def asset(asset_id="pixabay:1", *, kind="free", title="Night market", author="Ann",
          label="可自由使用", name="Pixabay License", hint="Ann（Pixabay）"):
    return {"kind": "asset", "asset_id": asset_id, "title": title, "author": author,
            "webpage_url": f"https://p/{asset_id}", "credit_hint": hint,
            "license": {"kind": kind, "label": label, "name": name}}


def build(scenes, music=None, title="台北夜市"):
    sources = {"scenes": [{"scene": sid, "source": src} for sid, src in scenes],
               "music": None if music is None else {"offset": 0, "looped": False, "source": music}}
    return credits.build("night-01", title, sources)


def test_same_asset_used_in_several_scenes_is_listed_once_with_all_its_scenes():
    text, summary = build([("s1", asset()), ("s2", asset("pixabay:2", author="Bo")),
                           ("s3", asset())])
    assert text.count("「Night market」") == 2          # 兩個不同的素材（同標題、不同 id）
    assert "（場景 s1、s3）" in text and "（場景 s2）" in text
    assert summary["assets"] == 2


def test_attribution_required_and_unclear_licences_are_called_out():
    text, summary = build(
        [("s1", asset(kind="free-attribution", label="CC BY", name="CC BY 4.0")),
         ("s2", asset("pixabay:2", kind="check", label="請確認授權", name=""))])
    assert "**必須標註**" in text
    assert "⚠️ 請確認授權" in text
    assert summary == {"assets": 2, "attribution_required": ["pixabay:1"],
                       "unclear": ["pixabay:2"]}


def test_local_files_are_listed_by_name_and_flagged_for_a_licence_check():
    text, summary = build([("s1", {"kind": "file", "path": "/a/b/clip.mp4"})])
    assert "「clip.mp4」" in text and "⚠️ 本機檔案，授權請自行確認" in text
    assert summary["unclear"] == ["/a/b/clip.mp4"]
    assert "素材來源：" not in text      # 本機檔案沒有可以貼的出處


def test_music_has_its_own_section_and_its_credit_goes_into_the_description_block():
    music = asset("openverse:m1", title="Calm piano", author="Bob", kind="free-attribution",
                  label="CC BY", name="CC BY 4.0", hint="Bob（CC BY 版本）— https://o/m1")
    text, summary = build([("s1", asset())], music)
    assert "## 背景音樂" in text and "「Calm piano」— Bob" in text
    block = text.split("```")[1]
    assert "Ann（Pixabay）" in block and "Bob（CC BY 版本）— https://o/m1" in block
    assert summary["attribution_required"] == ["openverse:m1"]


def test_description_block_falls_back_to_author_and_link_without_a_credit_hint():
    text, _ = build([("s1", asset(hint=""))])
    assert "Ann — https://p/pixabay:1" in text.split("```")[1]


def test_an_untitled_short_uses_its_id_and_no_sources_says_so():
    text, summary = build([("s1", None)], title="")
    assert text.startswith("# 素材來源與授權：night-01")
    assert "沒有用到外部素材" in text and summary["assets"] == 0


def test_the_short_summary_counts_assets_and_the_licences_that_need_a_look(tmp_path):
    import json

    from vidpipe import shorts
    from vidpipe.config import Config

    cfg = Config(work_dir=str(tmp_path / "work"), out_dir=str(tmp_path / "out"))
    root = tmp_path / "work" / "shorts" / "night-01"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"title": "台北夜市", "scenes": [{}, {}]}),
                                        encoding="utf-8")
    assert shorts.summarize(cfg, "night-01")["credits"] is None        # 還沒渲染過
    sources = {"scenes": [
        {"scene": "s1", "source": asset(kind="free-attribution")},
        {"scene": "s2", "source": asset("pixabay:2", kind="check")},
        {"scene": "s3", "source": {"kind": "file", "path": "/a/b/clip.mp4"}}],
        "music": {"offset": 0, "looped": False, "source": asset("jamendo:1", kind="unknown")}}
    (root / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    assert shorts.summarize(cfg, "night-01")["credits"] == {
        "assets": 4, "attribution_required": 1, "unclear": 3}
