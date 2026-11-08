from __future__ import annotations

import json
import subprocess
import sys

import pytest

from vidpipe import cli, config
from vidpipe.errors import BadInput, ConfigError, UpstreamError, VidpipeError, from_exception


def test_error_shape_matches_vidgrab():
    err = BadInput("壞了", hint="這樣改")
    assert err.to_dict() == {
        "ok": False,
        "error": {"code": "bad_input", "message": "壞了", "retryable": False, "hint": "這樣改"},
    }
    assert UpstreamError("連不上").to_dict()["error"]["retryable"] is True


def test_from_exception_wraps_unknown():
    err = from_exception(ValueError("boom"))
    assert isinstance(err, VidpipeError)
    assert err.code == "internal" and err.retryable and "boom" in err.message
    keep = BadInput("x")
    assert from_exception(keep) is keep


def test_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDPIPE_CONFIG", str(tmp_path / "none.json"))
    cfg = config.load()
    assert cfg.tts_base_url == "http://127.0.0.1:8301"
    assert cfg.burn_subtitles is True and cfg.subtitle_langs == "zh-TW"
    assert cfg.tts_model == ""  # 模型尚未決定，不寫死


def test_config_file_then_env_override(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"tts_model": "cosy", "burn_subtitles": False,
                                "vidgrab_url": "http://h:1/"}), encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_CONFIG", str(path))
    cfg = config.load()
    assert cfg.tts_model == "cosy" and cfg.burn_subtitles is False
    assert cfg.vidgrab_url == "http://h:1"  # 去掉結尾斜線
    monkeypatch.setenv("VIDPIPE_TTS_MODEL", "other")
    monkeypatch.setenv("VIDPIPE_BURN_SUBTITLES", "true")
    cfg = config.load()
    assert cfg.tts_model == "other" and cfg.burn_subtitles is True


@pytest.mark.parametrize("payload,needle", [
    ({"nope": 1}, "不認得"),
    ({"subtitle_langs": "fr"}, "subtitle_langs"),
    ({"burn_subtitles": "maybe"}, "true/false"),
])
def test_config_rejects_bad_values(tmp_path, monkeypatch, payload, needle):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_CONFIG", str(path))
    with pytest.raises(ConfigError, match=needle):
        config.load()


def test_config_rejects_non_json(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_CONFIG", str(path))
    with pytest.raises(ConfigError):
        config.load()


def test_bilingual_subtitles_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDPIPE_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv("VIDPIPE_SUBTITLE_LANGS", "zh-TW+en")
    assert config.load().subtitle_langs == "zh-TW+en"


def test_cli_config_masks_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDPIPE_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setenv("VIDPIPE_VIDGRAB_TOKEN", "super-secret")
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "super-secret" not in out
    assert json.loads(out)["config"]["vidgrab_token"] == "***"


def test_cli_version_is_json(capsys, monkeypatch):
    monkeypatch.setenv("VIDPIPE_BUILD", "7.abc1234")
    assert cli.main(["version"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["build"] == "7.abc1234" and data["full"].endswith("+7.abc1234")


def test_cli_errors_are_structured_json(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("VIDPIPE_CONFIG", str(path))
    assert cli.main(["config"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["error"]["code"] == "bad_config"


def test_module_entrypoint_runs():
    proc = subprocess.run([sys.executable, "-m", "vidpipe", "version"],
                          capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0 and json.loads(proc.stdout)["ok"] is True
