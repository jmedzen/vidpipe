"""排版模板：驗證、內建模板、存與刪、套用到 manifest，以及字幕樣式（顏色、位置、底框、字體）。"""
from __future__ import annotations

import json

import pytest

from vidpipe import cli, quick, subtitles, templates
from vidpipe import manifest as manifest_mod
from vidpipe.colors import ass_color, is_color
from vidpipe.config import Config
from vidpipe.errors import BadInput, NotFound
from vidpipe.video import OutputSpec

TEXT = "為什麼越想放下，反而越放不下？\n\n今晚念頭又來，就說一聲，我看到了。"


def cfg_in(tmp_path) -> Config:
    return Config(template_dir=str(tmp_path / "templates"))


# ------------------------------------------------------------------ 字幕樣式

def ass_for(**style) -> str:
    look = manifest_mod.Subtitles(**style)
    layout = subtitles.layout_for(OutputSpec(), look.size, look.margin)
    entry = subtitles.Entry(0.0, 1.0, ("你好",), ("hello",))
    return subtitles.to_ass([entry], layout, "F", style_of=look)


def test_default_style_lines_are_exactly_what_they_were_before_templates():
    ass = ass_for()
    assert ("Style: Default,F,65,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,"
            "100,100,0,0,1,5,1,2,76,76,384,1") in ass
    assert ("Style: En,F,40,&H0066E0FF,&H0066E0FF,&H00000000,&H78000000,-1,0,0,0,"
            "100,100,0,0,1,5,1,2,76,76,384,1") in ass
    assert ass == subtitles.to_ass([subtitles.Entry(0.0, 1.0, ("你好",), ("hello",))],
                                   subtitles.layout_for(OutputSpec()), "F")


def test_colors_margin_and_box_change_the_style_lines():
    ass = ass_for(color="cyan", en_color="#102030", margin=0.3, box=True)
    default = next(line for line in ass.splitlines() if line.startswith("Style: Default"))
    fields = default.split(",")
    assert fields[3] == ass_color("cyan") and fields[5] == subtitles.BOX_COLOR
    assert fields[15] == "3" and fields[-2] == str(round(1920 * 0.3))     # 底框、離底 30%
    assert ",&H00302010," in ass                                          # #RRGGBB → BGR


def test_color_names_keep_the_values_that_used_to_be_hard_coded():
    assert ass_color("white") == "&H00FFFFFF"
    assert ass_color("yellow") == "&H0033DDFF"
    assert ass_color("paleyellow") == "&H0066E0FF"
    assert is_color("#a1B2c3") and not is_color("#12345") and not is_color("red")


def test_manifest_accepts_style_fields_and_rejects_bad_ones():
    base = {"schema": manifest_mod.SCHEMA, "id": "x", "scenes": [{"id": "a", "text": "你好。"}]}
    m = manifest_mod.parse({**base, "subtitles": {"color": "pink", "box": True, "margin": 0.25,
                                                  "font": "PingFang TC"},
                            "overlays": [{"text": "標題", "color": "#FFFFFF"}]})
    assert (m.subtitles.color, m.subtitles.box, m.subtitles.margin, m.subtitles.font) == \
        ("pink", True, 0.25, "PingFang TC")
    for bad, needle in (({"color": "red"}, "subtitles.color"), ({"margin": 0.9}, "margin"),
                        ({"box": "yes"}, "box"), ({"font": "A,B"}, "font")):
        with pytest.raises(BadInput) as err:
            manifest_mod.parse({**base, "subtitles": bad})
        assert needle in err.value.message


def test_a_manifest_font_overrides_the_configured_font():
    from types import SimpleNamespace

    from vidpipe.timeline import Cue

    tl = SimpleNamespace(cues=[Cue(0, "a", "你好", "", 0.0, 0.8, "a.wav")], total=1.0)
    look = manifest_mod.Subtitles(font="PingFang TC")
    subs = subtitles.build(tl, look, OutputSpec(), "Noto Sans CJK TC")
    assert "Style: Default,PingFang TC," in subs.ass


# ------------------------------------------------------------------ 模板

def test_builtins_are_valid_and_classic_comes_first():
    found = templates.builtin()
    assert list(found)[0] == "classic" and {"clean", "bold", "zen"} <= set(found)
    classic = found["classic"]
    assert {k: classic[k] for k in templates.BASE} == templates.BASE


def test_classic_builds_the_same_layout_quick_used_to_hard_code():
    parts = templates.manifest_parts(templates.get(None), title="標題", series="系列")
    assert parts["overlays"] == [
        {"text": "標題", "position": "top", "color": "yellow", "size": "xlarge"},
        {"text": "系列", "position": "bottom", "size": "small"}]
    assert parts["subtitles"] == {"burn_in": True, "size": "large"}
    assert (parts["fit"], parts["aspect"], parts["music_volume"]) == ("crop", "9:16", 0.22)
    landscape = templates.manifest_parts(templates.get("classic"), title="T", aspect="16:9")
    assert landscape["overlays"][0]["size"] == "medium" and len(landscape["overlays"]) == 1


def test_every_builtin_produces_a_valid_manifest():
    for name, tpl in templates.builtin().items():
        parts = templates.manifest_parts(tpl, title="標題", series="系列")
        doc = {"schema": manifest_mod.SCHEMA, "id": f"t-{name}",
               "output": {"aspect": parts["aspect"]}, "subtitles": parts["subtitles"],
               "overlays": parts["overlays"],
               "scenes": [{"id": "a", "text": "你好。", "fit": parts["fit"]}]}
        manifest_mod.parse(doc)


@pytest.mark.parametrize("doc, needle", [
    ({"name": "x"}, "schema"),
    ({"schema": templates.SCHEMA, "name": "Bad Name"}, "name"),
    ({"schema": templates.SCHEMA, "name": "x", "colour": 1}, "colour"),
    ({"schema": templates.SCHEMA, "name": "x", "subtitles": {"color": "red"}}, "subtitles.color"),
    ({"schema": templates.SCHEMA, "name": "x", "subtitles": {"margin": 2}}, "subtitles.margin"),
    ({"schema": templates.SCHEMA, "name": "x", "title": {"size": "huge"}}, "title.size"),
    ({"schema": templates.SCHEMA, "name": "x", "title": {"shown": True}}, "shown"),
    ({"schema": templates.SCHEMA, "name": "x", "fit": "zoom"}, "fit"),
    ({"schema": templates.SCHEMA, "name": "x", "music": {"volume": 5}}, "music.volume")])
def test_bad_templates_say_which_field(doc, needle):
    with pytest.raises(BadInput) as err:
        templates.parse(doc)
    assert needle in err.value.message + err.value.hint


def test_save_list_get_and_delete_a_custom_template(tmp_path):
    cfg = cfg_in(tmp_path)
    saved = templates.save({"name": "mine", "label": "我的", "subtitles": {"box": True}}, cfg)
    on_disk = json.loads((tmp_path / "templates" / "mine.json").read_text("utf-8"))
    assert on_disk == {"schema": templates.SCHEMA, "name": "mine", "label": "我的",
                       "subtitles": {"box": True}}                    # 只存跟 classic 不同的
    assert saved["template"]["subtitles"]["box"] is True
    assert templates.get("mine", cfg)["label"] == "我的"
    names = [(i["name"], i["builtin"]) for i in templates.list_all(cfg)]
    assert names[0] == ("classic", True) and names[-1] == ("mine", False)
    renamed = templates.save(on_disk, cfg, name="mine-2")
    assert renamed["name"] == "mine-2" and (tmp_path / "templates" / "mine-2.json").is_file()
    templates.delete("mine", cfg)
    with pytest.raises(NotFound):
        templates.get("mine", cfg)
    with pytest.raises(BadInput):
        templates.save({"name": "classic"}, cfg)
    with pytest.raises(BadInput):
        templates.delete("classic", cfg)


def test_a_broken_custom_file_is_listed_with_its_error(tmp_path):
    cfg = cfg_in(tmp_path)
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "oops.json").write_text("{", encoding="utf-8")
    (tmp_path / "templates" / "bold.json").write_text(
        json.dumps({"schema": templates.SCHEMA, "name": "bold"}), encoding="utf-8")
    errors = {i["name"]: i.get("error", "") for i in templates.list_all(cfg) if not i["builtin"]}
    assert "JSON" in errors["oops"] and "內建" in errors["bold"]


def test_a_template_can_be_given_as_a_file_path(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"schema": templates.SCHEMA, "name": "x", "fit": "letterbox"}),
                    encoding="utf-8")
    assert templates.get(str(path))["fit"] == "letterbox"


# ------------------------------------------------------------------ 文案直出

def test_quick_request_takes_its_defaults_from_the_template(tmp_path):
    cfg = cfg_in(tmp_path)
    plain = quick.parse_request({"text": TEXT}, cfg=cfg)
    assert (plain["template"], plain["subtitle_size"], plain["aspect"]) == \
        ("classic", "large", "9:16")
    bold = quick.parse_request({"text": TEXT, "template": "bold"}, cfg=cfg)
    assert (bold["template"], bold["subtitle_size"]) == ("bold", "xlarge")
    mine = quick.parse_request({"text": TEXT, "template": "bold", "subtitle_size": "normal"},
                               cfg=cfg)
    assert mine["subtitle_size"] == "normal"                        # 文案自己寫的優先
    with pytest.raises(BadInput) as err:
        quick.parse_request({"text": TEXT, "template": "nope"}, cfg=cfg)
    assert "template" in err.value.message
    from dataclasses import replace

    zen_default = quick.parse_request({"text": TEXT}, cfg=replace(cfg, default_template="zen"))
    assert zen_default["template"] == "zen"


def test_batch_shares_the_template_option(tmp_path):
    reqs = quick.parse_batch([{"text": "一。"}, {"text": "二。", "template": "zen"}],
                             shared={"template": "clean"}, cfg=cfg_in(tmp_path))
    assert [r["template"] for r in reqs] == ["clean", "zen"]


def test_cli_templates_lists_shows_saves_and_deletes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDPIPE_TEMPLATE_DIR", str(tmp_path / "t"))

    def run(*argv, expect=0):
        capsys.readouterr()
        code = cli.main(list(argv))
        out = json.loads(capsys.readouterr().out)
        assert code == expect, out
        return out

    assert [i["name"] for i in run("templates")["items"]][:1] == ["classic"]
    assert run("templates", "show", "bold")["template"]["subtitles"]["size"] == "xlarge"
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"name": "x", "title": {"show": False}}), encoding="utf-8")
    assert run("templates", "save", str(src), "--name", "mine")["name"] == "mine"
    assert run("templates", "show", "mine")["template"]["title"]["show"] is False
    assert run("templates", "delete", "mine")["deleted"]
    assert run("templates", "delete", "classic", expect=2)["ok"] is False


def test_http_lists_saves_and_deletes_templates(tmp_path, monkeypatch):
    from test_server import Running

    from vidpipe import server

    monkeypatch.setenv("VIDPIPE_TEMPLATE_DIR", str(tmp_path / "t"))
    run = Running(server.make_server("127.0.0.1", 0))
    try:
        body = run.get("/api/templates").json()
        assert body["ok"] and body["items"][0]["name"] == "classic" and "cyan" in body["colors"]
        saved = run.put("/api/templates/mine",
                        json={"template": {"label": "我的", "subtitles": {"box": True}}})
        assert saved.status_code == 200 and saved.json()["name"] == "mine"
        assert any(i["name"] == "mine" for i in run.get("/api/templates").json()["items"])
        builtin = run.put("/api/templates/classic", json={"template": {}})
        assert builtin.status_code == 400
        assert run.delete("/api/templates/mine").json()["deleted"]
        assert run.delete("/api/templates/mine").status_code == 404
    finally:
        run.httpd.shutdown()
        run.httpd.server_close()
