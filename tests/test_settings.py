"""設定：欄位說明與驗證、讀（來源、祕密）、寫（原子、備份、衝突）、測試連線。"""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import subprocess
from dataclasses import fields

import pytest
from mediagen import font_name, has_cjk_font
from ttsfake import FakeTTS

from vidpipe import config as cfgmod
from vidpipe import settings
from vidpipe.config import SECRET_FIELDS, SPECS, Config
from vidpipe.errors import ConfigError, FieldErrors


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "conf" / "vidpipe.json"
    monkeypatch.setenv("VIDPIPE_CONFIG", str(path))
    return path


def by_name(desc):
    return {f["name"]: f for f in desc["fields"]}


# ------------------------------------------------------------ 欄位說明

def test_every_config_field_has_exactly_one_spec():
    assert {f.name for f in fields(Config)} == set(SPECS)
    assert {n for n, s in SPECS.items() if s.secret} == SECRET_FIELDS
    groups = {g.id for g in cfgmod.GROUPS}
    for spec in SPECS.values():
        assert spec.group in groups, spec.name
        assert not spec.depends_on or spec.depends_on in SPECS, spec.name
        assert spec.label and spec.help, spec.name


def test_defaults_pass_their_own_validation():
    for name in SPECS:
        if name == "vidgrab_url":
            continue
        assert cfgmod.check_field(name, getattr(Config(), name)) == getattr(Config(), name)
    assert cfgmod.check_field("vidgrab_url", Config().vidgrab_url) == Config().vidgrab_url


@pytest.mark.parametrize("name,value,message", [
    ("vidgrab_url", "127.0.0.1:8310", "http://"),
    ("vidgrab_url", "ftp://host", "http://"),
    ("vidgrab_url", "http://", "http://"),
    ("vidgrab_url", "", "空的"),
    ("vidgrab_url", "http://a b", "空白"),
    ("vidgrab_url", "http://u:p@host:1", "帳號密碼"),
    ("vidgrab_url", "http://host:1/?x=1", r"\? 或 #"),
    ("vidgrab_url", "http://host:99999", "port"),
    ("tts_base_url", "localhost", "http://"),
    ("tts_concurrency", 0, "1–16"),
    ("tts_concurrency", 17, "1–16"),
    ("tts_concurrency", "abc", "整數"),
    ("tts_concurrency", 1.5, "整數"),
    ("tts_concurrency", True, "整數"),
    ("target_lufs", -4, "-30 到 -5"),
    ("target_lufs", "loud", "數字"),
    ("target_lufs", float("nan"), "-30 到 -5"),
    ("subtitle_langs", "en", "不認得"),
    ("subtitle_font", "A,B", "逗號"),
    ("subtitle_font", "  ", "不能是空的"),
    ("burn_subtitles", "maybe", "true/false"),
    ("work_dir", "", "不能是空的"),
    ("tts_model", ["a"], "格式不對"),
])
def test_check_field_rejects_with_a_readable_message(name, value, message):
    with pytest.raises(ConfigError, match=message):
        cfgmod.check_field(name, value)


def test_check_field_normalises_values():
    assert cfgmod.check_field("vidgrab_url", " http://h:1/// ") == "http://h:1"
    assert cfgmod.check_field("tts_model", "  m  ") == "m"
    assert cfgmod.check_field("burn_subtitles", "off") is False
    assert cfgmod.check_field("target_lufs", "-16") == -16.0
    assert cfgmod.check_field("tts_concurrency", "4") == 4
    assert cfgmod.check_field("vidgrab_token", "abc\n") == "abc"


def test_url_errors_do_not_echo_credentials():
    for bad in ("user:hunter2@host:8310", "ftp://user:hunter2@host", "http://u:hunter2@h:1"):
        with pytest.raises(ConfigError) as err:
            cfgmod.check_field("vidgrab_url", bad)
        assert "hunter2" not in err.value.message + err.value.hint


# ------------------------------------------------------------ 環境變數、設定檔、預設

def test_empty_env_var_counts_as_unset(cfg_file, monkeypatch):
    cfg_file.parent.mkdir()
    cfg_file.write_text(json.dumps({"tts_model": "from-file", "burn_subtitles": False}),
                        encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_TTS_MODEL", "")           # docker compose 的 ${VAR:-}
    monkeypatch.setenv("VIDPIPE_BURN_SUBTITLES", "")
    cfg = cfgmod.load()
    assert cfg.tts_model == "from-file" and cfg.burn_subtitles is False
    monkeypatch.setenv("VIDPIPE_TTS_MODEL", "from-env")
    assert cfgmod.load().tts_model == "from-env"


def test_load_errors_say_where_the_bad_value_came_from(cfg_file, monkeypatch):
    monkeypatch.setenv("VIDPIPE_TTS_CONCURRENCY", "99")
    with pytest.raises(ConfigError) as env_err:
        cfgmod.load()
    assert "VIDPIPE_TTS_CONCURRENCY" in env_err.value.hint
    monkeypatch.delenv("VIDPIPE_TTS_CONCURRENCY")
    cfg_file.parent.mkdir()
    cfg_file.write_text('{"tts_concurrency": 99}', encoding="utf-8")
    with pytest.raises(ConfigError) as file_err:
        cfgmod.load()
    assert str(cfg_file) in file_err.value.hint


def test_describe_defaults(cfg_file):
    desc = settings.describe()
    assert desc["path"] == str(cfg_file.resolve()) and desc["path_from_env"] is True
    assert desc["exists"] is False and desc["revision"] == "" and desc["writable"] is True
    assert desc["problems"] == [] and desc["load_error"] == ""
    fields_ = by_name(desc)
    assert list(fields_) == list(SPECS)
    assert all(f["source"] == "default" and not f["locked"] for f in fields_.values())
    assert fields_["tts_concurrency"]["value"] == 1 and fields_["tts_concurrency"]["max"] == 16
    assert fields_["subtitle_langs"]["choices"][1]["value"] == "zh-TW+en"
    assert fields_["tts_token"]["value"] is None and fields_["tts_token"]["is_set"] is False
    assert [g["id"] for g in desc["groups"]] == ["vidgrab", "tts", "subtitles", "audio", "storage"]


def test_describe_tells_env_from_file_from_default(cfg_file, monkeypatch):
    cfg_file.parent.mkdir()
    cfg_file.write_text(json.dumps({"tts_model": "file-model", "vidgrab_url": "http://file:1",
                                    "burn_subtitles": False}), encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", "http://env:2")
    f = by_name(settings.describe())
    assert f["vidgrab_url"]["source"] == "env" and f["vidgrab_url"]["locked"]
    assert f["vidgrab_url"]["value"] == "http://env:2"
    assert f["vidgrab_url"]["file_value"] == "http://file:1"      # 被環境變數蓋住的那份
    assert f["vidgrab_url"]["env_var"] == "VIDPIPE_VIDGRAB_URL"
    assert f["tts_model"]["source"] == "file" and f["tts_model"]["in_file"]
    assert f["tts_model"]["value"] == "file-model"
    assert f["burn_subtitles"]["value"] is False
    assert f["subtitle_font"]["source"] == "default" and not f["subtitle_font"]["in_file"]


def test_secrets_never_leave_the_server(cfg_file, monkeypatch):
    cfg_file.parent.mkdir()
    cfg_file.write_text(json.dumps({"tts_token": "file-secret-1"}), encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_VIDGRAB_TOKEN", "env-secret-2")
    desc = settings.describe()
    text = json.dumps(desc, ensure_ascii=False)
    assert "file-secret-1" not in text and "env-secret-2" not in text
    f = by_name(desc)
    assert f["tts_token"]["source"] == "file" and f["tts_token"]["is_set"] is True
    assert f["tts_token"]["value"] is None and f["tts_token"]["file_value"] is None
    assert f["vidgrab_token"]["source"] == "env" and f["vidgrab_token"]["is_set"] is True


def test_describe_still_works_when_the_file_is_broken(cfg_file, monkeypatch):
    cfg_file.parent.mkdir()
    cfg_file.write_text("{not json", encoding="utf-8")
    desc = settings.describe()
    assert [p["code"] for p in desc["problems"]] == ["file_invalid"]
    assert desc["load_error"] and len(desc["fields"]) == len(SPECS)
    cfg_file.write_text(json.dumps({"tts_model": "m", "oops": 1, "typo_key": 2}), encoding="utf-8")
    desc = settings.describe()
    unknown = [p for p in desc["problems"] if p["code"] == "unknown_keys"][0]
    assert unknown["keys"] == ["oops", "typo_key"] and "備份" in unknown["hint"]
    assert by_name(desc)["tts_model"]["value"] == "m"


def test_describe_flags_values_that_fail_validation(cfg_file, monkeypatch):
    cfg_file.parent.mkdir()
    cfg_file.write_text(json.dumps({"target_lufs": 3, "tts_concurrency": [1]}), encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", "not-a-url")
    f = by_name(settings.describe())
    assert "-30 到 -5" in f["target_lufs"]["problem"] and f["target_lufs"]["value"] == 3
    assert f["tts_concurrency"]["problem"] and isinstance(f["tts_concurrency"]["value"], str)
    assert f["vidgrab_url"]["problem"] and f["vidgrab_url"]["locked"]
    assert f["tts_model"]["problem"] == ""


def test_describe_reports_unwritable_locations(cfg_file, monkeypatch):
    monkeypatch.setattr(settings.os, "access", lambda *_a, **_k: False)
    desc = settings.describe()
    assert desc["writable"] is False and str(cfg_file.parent.parent) in desc["write_problem"]
    cfg_file.parent.mkdir()
    cfg_file.write_text("{}", encoding="utf-8")
    assert "不可寫" in settings.describe()["write_problem"]
    monkeypatch.undo()


def test_readonly_switch(cfg_file, monkeypatch):
    monkeypatch.setenv(settings.READONLY_ENV, "1")
    desc = settings.describe()
    assert desc["readonly"] is True and desc["writable"] is False
    with pytest.raises(settings.Forbidden):
        settings.apply({"tts_model": "x"})
    assert not cfg_file.exists()


# ------------------------------------------------------------ 儲存

def test_apply_writes_the_config_file_the_loader_reads(cfg_file):
    out = settings.apply({"tts_model": "cosy", "tts_concurrency": "3", "burn_subtitles": False,
                          "vidgrab_url": "http://h:9/"})
    assert out["changed"] == ["burn_subtitles", "tts_concurrency", "tts_model", "vidgrab_url"]
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {
        "vidgrab_url": "http://h:9", "tts_model": "cosy", "tts_concurrency": 3,
        "burn_subtitles": False}
    cfg = cfgmod.load()
    assert (cfg.tts_model, cfg.tts_concurrency, cfg.burn_subtitles) == ("cosy", 3, False)
    assert by_name(out)["tts_model"]["source"] == "file"
    assert out["revision"] == settings.revision_of(cfg_file) != ""


def test_saved_file_is_private_and_leaves_no_temp_files(cfg_file):
    settings.apply({"tts_token": "s3cret"})
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600
    assert [p.name for p in cfg_file.parent.iterdir()] == [cfg_file.name]


def test_reset_removes_the_key_and_unchanged_saves_do_not_write(cfg_file):
    settings.apply({"tts_model": "m", "subtitle_font": "PingFang TC"})
    before = cfg_file.stat().st_mtime_ns
    assert settings.apply({"tts_model": "m"})["changed"] == []     # 沒變：不寫檔
    assert cfg_file.stat().st_mtime_ns == before
    out = settings.apply({"subtitle_font": None})
    assert out["changed"] == ["subtitle_font"]
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"tts_model": "m"}
    assert by_name(out)["subtitle_font"]["source"] == "default"


def test_nothing_to_save_does_not_create_the_file(cfg_file):
    assert settings.apply({})["changed"] == [] and not cfg_file.exists()
    assert settings.apply({"tts_model": None})["changed"] == [] and not cfg_file.exists()


def test_secrets_set_keep_and_clear(cfg_file):
    settings.apply({"tts_token": "  tok-1\n", "vidgrab_token": "vg-1"})
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["tts_token"] == "tok-1" and data["vidgrab_token"] == "vg-1"
    settings.apply({"tts_model": "m"})                                  # 沒提到 token = 不動
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["tts_token"] == "tok-1"
    settings.apply({"tts_token": ""})                                   # 空字串 = 清掉
    settings.apply({"vidgrab_token": None})                             # None = 清掉
    assert "token" not in cfg_file.read_text(encoding="utf-8")
    with pytest.raises(FieldErrors) as err:
        settings.apply({"tts_token": 123})
    assert "tts_token" in err.value.fields


def test_invalid_fields_are_reported_together_and_nothing_is_written(cfg_file):
    settings.apply({"tts_model": "keep"})
    before = cfg_file.read_text(encoding="utf-8")
    with pytest.raises(FieldErrors) as err:
        settings.apply({"tts_model": "new", "tts_concurrency": 99, "vidgrab_url": "nope",
                        "bogus": 1})
    fields_ = err.value.fields
    assert set(fields_) == {"tts_concurrency", "vidgrab_url", "bogus"}
    assert "1–16" in fields_["tts_concurrency"] and "http://" in fields_["vidgrab_url"]
    assert err.value.to_dict()["error"]["fields"] == fields_
    assert err.value.code == "bad_input"
    assert cfg_file.read_text(encoding="utf-8") == before


def test_env_pinned_fields_cannot_be_saved(cfg_file, monkeypatch):
    monkeypatch.setenv("VIDPIPE_TTS_BASE_URL", "http://pinned:1")
    with pytest.raises(FieldErrors) as err:
        settings.apply({"tts_base_url": "http://other:2", "tts_model": "m"})
    assert "VIDPIPE_TTS_BASE_URL" in err.value.fields["tts_base_url"]
    assert not cfg_file.exists()


def test_stale_revision_is_refused(cfg_file):
    first = settings.apply({"tts_model": "a"})
    settings.apply({"tts_model": "b"})                                  # 別的視窗改過了
    with pytest.raises(settings.StaleConfig) as err:
        settings.apply({"tts_model": "mine"}, revision=first["revision"])
    assert err.value.code == "stale_config"
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["tts_model"] == "b"
    current = settings.describe()["revision"]
    assert settings.apply({"tts_model": "mine"}, revision=current)["changed"] == ["tts_model"]
    with pytest.raises(settings.StaleConfig):         # 已經有檔案了，空字串的 revision 是舊的
        settings.apply({"tts_model": "x"}, revision="")
    assert settings.apply({"tts_model": "y"}, revision=None)["changed"] == ["tts_model"]


def test_broken_file_needs_an_explicit_replace_and_is_backed_up(cfg_file):
    cfg_file.parent.mkdir()
    cfg_file.write_text("{broken: 'with a token'", encoding="utf-8")
    with pytest.raises(settings.jobs.Conflict) as err:
        settings.apply({"tts_model": "m"})
    assert "備份" in err.value.hint
    assert cfg_file.read_text(encoding="utf-8").startswith("{broken")
    out = settings.apply({"tts_model": "m"}, replace_invalid=True)
    assert out["problems"] == [] and out["changed"] == ["tts_model"]
    bak = cfg_file.with_name(cfg_file.name + ".bak")
    assert bak.read_text(encoding="utf-8") == "{broken: 'with a token'"
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"tts_model": "m"}


def test_unknown_keys_are_dropped_after_a_backup(cfg_file):
    cfg_file.parent.mkdir()
    original = json.dumps({"tts_model": "m", "old_option": True})
    cfg_file.write_text(original, encoding="utf-8")
    out = settings.apply({"burn_subtitles": False})
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"tts_model": "m",
                                                                "burn_subtitles": False}
    assert cfg_file.with_name(cfg_file.name + ".bak").read_text(encoding="utf-8") == original
    assert out["problems"] == [] and cfgmod.load().tts_model == "m"


def test_a_failed_write_leaves_the_old_file_and_no_temp_files(cfg_file, monkeypatch):
    settings.apply({"tts_model": "old"})

    def boom(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(settings.os, "replace", boom)
    monkeypatch.setattr(settings.os, "access", lambda *_a, **_k: False)   # 不准退回原地覆寫
    with pytest.raises(OSError):
        settings.apply({"tts_model": "new"})
    monkeypatch.undo()
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"tts_model": "old"}
    assert [p.name for p in cfg_file.parent.iterdir()] == [cfg_file.name]


def test_bind_mounted_single_file_falls_back_to_in_place_write(cfg_file, monkeypatch):
    settings.apply({"tts_model": "old"})

    def busy(*_a, **_k):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(settings.os, "replace", busy)
    settings.apply({"tts_model": "new"})
    monkeypatch.undo()
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"tts_model": "new"}
    assert [p.name for p in cfg_file.parent.iterdir()] == [cfg_file.name]


def test_symlinked_config_keeps_the_link(tmp_path, monkeypatch):
    real = tmp_path / "dotfiles" / "vidpipe.json"
    real.parent.mkdir()
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "vidpipe.json"
    link.symlink_to(real)
    monkeypatch.setenv("VIDPIPE_CONFIG", str(link))
    settings.apply({"tts_model": "m"})
    assert link.is_symlink() and json.loads(real.read_text(encoding="utf-8")) == {"tts_model": "m"}


def test_saved_values_apply_to_the_next_load_without_restart(cfg_file, tmp_path):
    settings.apply({"work_dir": str(tmp_path / "w2"), "out_dir": str(tmp_path / "o2")})
    cfg = cfgmod.load()
    assert cfg.work_dir == str(tmp_path / "w2") and cfg.out_dir == str(tmp_path / "o2")


# ------------------------------------------------------------ 測試連線

def test_unknown_target_and_bad_values(cfg_file):
    with pytest.raises(settings.BadInput):
        settings.run_test("nope")
    with pytest.raises(settings.BadInput):
        settings.run_test("tts", ["x"])    # type: ignore[arg-type]


def test_tts_test_lists_models_and_uses_the_unsaved_draft(cfg_file, fake_server):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health"
        else (200, {"data": [{"id": "cosy"}, {"id": "other"}]}))
    settings.apply({"tts_base_url": "http://127.0.0.1:9"})              # 存的是沒人聽的 port
    down = settings.run_test("tts")
    assert down["ok"] is False and down["models"] == []
    out = settings.run_test("tts", {"tts_base_url": fake_server.url, "tts_model": "cosy"})
    assert out["ok"] is True and out["models"] == ["cosy", "other"] and out["target"] == "tts"
    assert out["elapsed_ms"] >= 0 and {c["name"] for c in out["checks"]} == {"tts", "voice_ref"}
    wrong = settings.run_test("tts", {"tts_base_url": fake_server.url, "tts_model": "nope"})
    assert wrong["ok"] is False and "cosy" in wrong["checks"][0]["detail"]
    assert wrong["models"] == ["cosy", "other"]
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == {
        "tts_base_url": "http://127.0.0.1:9"}                           # 測試不寫任何設定


def test_tts_test_with_synthesis(cfg_file, fake_server):
    tts = FakeTTS(fake_server, speech=lambda text: 0.5)
    base = fake_server.handler
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health"
        else (200, {"data": [{"id": "m"}]}) if p == "/v1/models" else base(m, p, h, b))
    out = settings.run_test("tts", {"tts_base_url": fake_server.url, "tts_model": "m",
                                    "voice_ref": "/tmp/ref.wav", "reference_text": "你好"},
                            deep=True)
    speak = [c for c in out["checks"] if c["name"] == "tts_speak"][0]
    assert speak["ok"] and "秒" in speak["detail"]
    assert tts.calls[0]["model"] == "m" and tts.calls[0]["voice_ref"] == "/tmp/ref.wav"
    assert tts.calls[0]["reference_text"] == "你好"
    assert not any(c["name"] == "tts_speak" for c in settings.run_test(
        "tts", {"tts_base_url": fake_server.url})["checks"])            # 預設不合成


def test_tts_synthesis_failure_is_reported(cfg_file, fake_server):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health"
        else (200, {"data": [{"id": "m"}]}) if p == "/v1/models"
        else (500, {"error": {"message": "voice_ref is required", "code": "bad_request"}}))
    out = settings.run_test("tts", {"tts_base_url": fake_server.url}, deep=True)
    speak = [c for c in out["checks"] if c["name"] == "tts_speak"][0]
    assert out["ok"] is False and not speak["ok"] and "voice_ref" in speak["detail"]


def test_vidgrab_test_reports_auth(cfg_file, fake_server):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"version": "1.2", "full": "1.2.99"}) if p == "/api/version"
        else (200, {"projects": []}) if h.get("authorization") == "Bearer good"
        else (401, {"ok": False, "error": {"code": "unauthorized", "message": "no"}}))
    ok = settings.run_test("vidgrab", {"vidgrab_url": fake_server.url, "vidgrab_token": "good"})
    assert ok["ok"] and "1.2.99" in ok["checks"][0]["detail"]
    bad = settings.run_test("vidgrab", {"vidgrab_url": fake_server.url, "vidgrab_token": "bad"})
    assert bad["ok"] is False and "token" in bad["checks"][0]["detail"]
    assert "bad" not in json.dumps(bad).replace("token", "")           # 回應裡沒有 token


def test_saved_secret_is_used_unless_the_draft_overrides_it(cfg_file, fake_server):
    fake_server.handler = lambda m, p, h, b: (200, {"version": "1", "projects": []})
    settings.apply({"vidgrab_token": "saved-token"})
    draft = {"vidgrab_url": fake_server.url}
    settings.run_test("vidgrab", draft)
    settings.run_test("vidgrab", {**draft, "vidgrab_token": None})      # None = 沒動
    settings.run_test("vidgrab", {**draft, "vidgrab_token": "typed-token"})
    settings.run_test("vidgrab", {**draft, "vidgrab_token": ""})        # 空字串 = 清掉
    auth = [h.get("authorization") for _m, p, h, _b in fake_server.calls
            if p == "/api/projects"]
    assert auth == ["Bearer saved-token", "Bearer saved-token", "Bearer typed-token", None]


def test_env_pinned_fields_ignore_the_draft(cfg_file, fake_server, monkeypatch):
    fake_server.handler = lambda m, p, h, b: (200, {"version": "pinned", "projects": []})
    monkeypatch.setenv("VIDPIPE_VIDGRAB_URL", fake_server.url)
    out = settings.run_test("vidgrab", {"vidgrab_url": "http://127.0.0.1:9"})
    assert out["ok"] and "pinned" in out["checks"][0]["detail"]


def test_invalid_draft_is_a_field_error_not_a_test_failure(cfg_file):
    with pytest.raises(FieldErrors) as err:
        settings.run_test("tts", {"tts_base_url": "nope", "tts_concurrency": 99})
    assert set(err.value.fields) == {"tts_base_url", "tts_concurrency"}


def test_readonly_ignores_the_draft_when_testing(cfg_file, fake_server, monkeypatch):
    fake_server.handler = lambda m, p, h, b: (200, {"status": "ok", "data": [{"id": "m"}]})
    settings.apply({"tts_base_url": "http://127.0.0.1:9"})
    monkeypatch.setenv(settings.READONLY_ENV, "1")
    out = settings.run_test("tts", {"tts_base_url": fake_server.url})  # 唯讀：草稿不能拿去打別處
    assert out["ok"] is False and fake_server.calls == []


def test_dirs_test_creates_nothing(cfg_file, tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    out = settings.run_test("dirs", {"work_dir": str(target), "out_dir": str(tmp_path)})
    work = out["checks"][0]["detail"]
    assert out["ok"] and "還不存在" in work and "GB" in work
    assert "可寫" in out["checks"][1]["detail"] and not (tmp_path / "does").exists()
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    bad = settings.run_test("dirs", {"work_dir": str(f)})
    assert bad["ok"] is False and "不是資料夾" in bad["checks"][0]["detail"]


def test_dirs_test_flags_unwritable(cfg_file, tmp_path, monkeypatch):
    monkeypatch.setattr(settings.os, "access", lambda *_a, **_k: False)
    out = settings.run_test("dirs", {"work_dir": str(tmp_path / "w")})
    assert out["ok"] is False and "不可寫" in out["checks"][0]["detail"]
    assert "uid 1000" in out["checks"][0]["hint"]


HAS_LIBASS = bool(shutil.which("ffmpeg")) and "subtitles" in (subprocess.run(
    ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
    check=False).stdout if shutil.which("ffmpeg") else "")


@pytest.mark.skipif(not HAS_LIBASS, reason="需要含 libass 的 ffmpeg")
def test_font_check_reads_what_libass_actually_picked():
    missing = settings.check_subtitle_font("Definitely Not A Real Font 12345")
    assert missing["ok"] is False and not missing["required"]
    assert "Definitely Not A Real Font 12345" in missing["detail"] and "安裝" in missing["hint"]


@pytest.mark.skipif(not (HAS_LIBASS and has_cjk_font()), reason="需要 libass 與 CJK 字型")
def test_font_check_accepts_an_installed_cjk_font():
    name = font_name()
    result = settings.check_subtitle_font(name)
    assert result["ok"] is True and name in result["detail"], result


@pytest.mark.skipif(not HAS_LIBASS, reason="需要含 libass 的 ffmpeg")
def test_ffmpeg_test_covers_binaries_filter_and_font(cfg_file):
    out = settings.run_test("ffmpeg", {"subtitle_font": "Definitely Not A Real Font 12345"})
    names = [c["name"] for c in out["checks"]]
    assert names[:3] == ["ffmpeg", "ffprobe", "ffmpeg_subtitles"] and "subtitle_font" in names
    assert out["ok"] is True       # 字體只是警告（required = False），不讓整項測試失敗


def test_font_name_matching():
    same = settings._same_font
    assert same("Noto Sans CJK TC", "NotoSansCJKtc-Regular", "/x/NotoSansCJK-Regular.ttc")
    assert same("PingFang TC", "PingFangTC-Regular", "/System/Library/PingFang.ttc")
    assert same("WenQuanYi Zen Hei", "WenQuanYiZenHei", "/usr/share/wqy-zenhei.ttc")
    assert not same("Noto Sans CJK TC", "Inter-Bold", "/usr/share/fonts/Inter-Bold.otf")
    assert not same("PingFang TC", "DejaVuSans", "/usr/share/DejaVuSans.ttf")


def test_font_check_parses_libass_log(monkeypatch):
    class Proc:
        returncode = 0
        stderr = ("[Parsed_ass_0 @ 0x1] fontselect: (Noto Sans CJK TC, 700, 0) -> "
                  "/usr/share/fonts/Inter-Bold.otf, 0, Inter-Bold\n"
                  "[Parsed_ass_0 @ 0x1] Glyph 0x5B57 not found, selecting one more font\n")

    monkeypatch.setattr(settings.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(settings.subprocess, "run", lambda *_a, **_k: Proc)
    bad = settings.check_subtitle_font("Noto Sans CJK TC")
    assert not bad["ok"] and "Inter-Bold" in bad["detail"]
    Proc.stderr = ("[Parsed_ass_0 @ 0x1] fontselect: (Noto Sans CJK TC, 700, 0) -> "
                   "/f/NotoSansCJK-Bold.ttc, 3, NotoSansCJKtc-Bold\n")
    assert settings.check_subtitle_font("Noto Sans CJK TC")["ok"]
    Proc.stderr = ("[Parsed_ass_0 @ 0x1] fontselect: (Arial, 700, 0) -> /f/Arial.ttf, 0, Arial\n"
                   "[Parsed_ass_0 @ 0x1] Glyph 0x5B57 not found, selecting one more font\n"
                   "[Parsed_ass_0 @ 0x1] fontselect: (Arial, 700, 0) -> /f/wqy.ttc, 0, WQY\n")
    glyph = settings.check_subtitle_font("Arial")
    assert not glyph["ok"] and "缺中文字形" in glyph["detail"] and "WQY" in glyph["detail"]
    Proc.stderr = ""
    assert settings.check_subtitle_font("Arial")["ok"]     # 沒有選字紀錄：不亂說「找不到」
    Proc.returncode, Proc.stderr = 1, "No such filter: 'ass'\n"
    assert "libass" in settings.check_subtitle_font("Arial")["detail"]


def test_no_secret_in_logs_or_errors(cfg_file, fake_server, capsys):
    fake_server.handler = lambda m, p, h, b: (401, {"error": {"message": "denied"}})
    settings.apply({"tts_token": "TOP-SECRET-TOKEN", "vidgrab_token": "ANOTHER-SECRET"})
    with pytest.raises(FieldErrors) as err:
        settings.apply({"tts_concurrency": 99, "tts_token": "TOP-SECRET-TOKEN-2"})
    out = settings.run_test("tts", {"tts_base_url": fake_server.url})
    captured = capsys.readouterr()
    everything = json.dumps(out) + json.dumps(err.value.to_dict()) + captured.out + captured.err
    for secret in ("TOP-SECRET-TOKEN", "ANOTHER-SECRET"):
        assert secret not in everything
    assert os.environ.get("VIDPIPE_TTS_TOKEN") is None
