from __future__ import annotations

import json
import shutil

import pytest

from vidpipe import cli, doctor
from vidpipe.clients import TTSClient, VidgrabClient
from vidpipe.config import Config
from vidpipe.errors import BadInput

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _cfg(tmp_path, **kw):
    return Config(work_dir=str(tmp_path / "work"), out_dir=str(tmp_path / "out"), **kw)


def _by_name(result):
    return {c["name"]: c for c in result["checks"]}


def test_check_binary_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    c = doctor.check_binary("ffmpeg")
    assert c["ok"] is False and c["required"] and "brew install ffmpeg" in c["hint"]


def test_subtitles_filter_required_only_when_burning(monkeypatch):
    class P:
        stdout = (" T.. drawtext          V->V       Draw text\n"
                  " ... subtitles         V->V       Render subtitles\n")

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(doctor, "_run", lambda *_a, **_k: P)
    assert doctor.check_subtitles_filter(Config())["ok"] is True

    P.stdout = " T.. drawtext          V->V       Draw text\n"
    burn = doctor.check_subtitles_filter(Config(burn_subtitles=True))
    assert burn["ok"] is False and burn["required"] is True
    no_burn = doctor.check_subtitles_filter(Config(burn_subtitles=False))
    assert no_burn["ok"] is False and no_burn["required"] is False


def test_cjk_font_found_via_fc_list(monkeypatch):
    class P:
        stdout = "Noto Sans CJK TC\n"

    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/fc-list" if n == "fc-list" else None)
    monkeypatch.setattr(doctor, "_run", lambda *_a, **_k: P)
    c = doctor.check_cjk_font()
    assert c["ok"] and not c["required"] and "Noto" in c["detail"]


def test_dirs_created_and_unwritable_reported(tmp_path):
    assert doctor.check_dirs(_cfg(tmp_path))["ok"] is True
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = doctor.check_dirs(Config(work_dir=str(blocker / "sub"), out_dir=str(tmp_path / "o")))
    assert bad["ok"] is False and "work_dir" in bad["detail"]


def test_vidgrab_ok_and_token_problem(fake_server):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"full": "0.2.0+9.abc"}) if p == "/api/version" else (200, []))
    c = doctor.check_vidgrab(VidgrabClient(fake_server.url, retries=0))
    assert c["ok"] and "0.2.0+9.abc" in c["detail"]

    def need_token(m, p, h, b):
        if p == "/api/version":
            return 200, {"version": "0.2.0"}
        return 401, {"ok": False, "error": {"code": "unauthorized", "message": "x",
                                            "retryable": False}}

    fake_server.handler = need_token
    c = doctor.check_vidgrab(VidgrabClient(fake_server.url, retries=0))
    assert c["ok"] is False and "token" in c["detail"] and "VIDPIPE_VIDGRAB_TOKEN" in c["hint"]


def test_vidgrab_unreachable():
    c = doctor.check_vidgrab(VidgrabClient("http://127.0.0.1:1", retries=0))
    assert c["ok"] is False and "連不上" in c["detail"]


def test_tts_checks(fake_server):
    def serve(models):
        def h(m, p, hd, b):
            if p == "/health":
                return 200, {"status": "ok"}
            if p == "/v1/models":
                return 200, {"data": models}
            return 404, {}
        return h

    fake_server.handler = serve([{"id": "cosy"}])
    assert doctor.check_tts(TTSClient(fake_server.url, retries=0), "cosy")["ok"]
    assert doctor.check_tts(TTSClient(fake_server.url, retries=0))["ok"]  # 沒指定模型也行

    wrong = doctor.check_tts(TTSClient(fake_server.url, retries=0), "other")
    assert wrong["ok"] is False and "cosy" in wrong["detail"]

    fake_server.handler = serve([])
    empty = doctor.check_tts(TTSClient(fake_server.url, retries=0))
    assert empty["ok"] is False and "沒有已載入的模型" in empty["detail"]
    assert "load" in empty["hint"]


def test_tts_without_models_endpoint_passes_on_health(fake_server):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health" else (404, {}))
    c = doctor.check_tts(TTSClient(fake_server.url, retries=0))
    assert c["ok"] and "略過" in c["detail"]


def test_voice_ref_is_only_a_warning(tmp_path):
    assert doctor.check_voice_ref(Config())["ok"] is True
    missing = doctor.check_voice_ref(Config(voice_ref="/nope/ref.wav"))
    assert missing["ok"] is False and missing["required"] is False
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    ok = doctor.check_voice_ref(Config(voice_ref=str(ref)))
    assert ok["ok"] and "reference_text" in ok["detail"]


def test_run_overall_ok_ignores_warnings_and_honours_skip(tmp_path, fake_server, monkeypatch):
    fake_server.handler = lambda m, p, h, b: (
        (200, {"status": "ok"}) if p == "/health"
        else (200, {"data": [{"id": "m"}]}) if p == "/v1/models"
        else (200, {"version": "1"}))
    monkeypatch.setattr(doctor, "check_binary", lambda n: doctor._check(n, True, "fake"))
    monkeypatch.setattr(doctor, "check_cjk_font", lambda: doctor._check("cjk_font", False,
                                                                        required=False))
    cfg = _cfg(tmp_path, burn_subtitles=False, vidgrab_url=fake_server.url,
               tts_base_url=fake_server.url)
    monkeypatch.setattr(doctor, "check_subtitles_filter",
                        lambda c: doctor._check("ffmpeg_subtitles", True))
    result = doctor.run(cfg)
    assert result["ok"] is True
    assert _by_name(result)["cjk_font"]["ok"] is False  # 警告不影響 ok

    skipped = doctor.run(cfg, skip=["tts", "vidgrab"])
    assert "tts" not in _by_name(skipped) and skipped["skipped"] == ["tts", "vidgrab"]


def test_run_fails_when_a_required_check_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "check_binary", lambda n: doctor._check(n, False, "x"))
    result = doctor.run(_cfg(tmp_path), skip=["vidgrab", "tts"])
    assert result["ok"] is False


def test_run_rejects_unknown_skip(tmp_path):
    with pytest.raises(BadInput, match="nope"):
        doctor.run(_cfg(tmp_path), skip=["nope"])


def test_cli_doctor_outputs_json_and_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDPIPE_WORK_DIR", str(tmp_path / "w"))
    monkeypatch.setenv("VIDPIPE_OUT_DIR", str(tmp_path / "o"))
    code = cli.main(["doctor", "--skip", "vidgrab,tts,ffmpeg,ffprobe,ffmpeg_subtitles"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["ok"] is True
    assert {c["name"] for c in data["checks"]} == {"cjk_font", "dirs", "voice_ref"}
    assert cli.main(["doctor", "--skip", "bogus"]) == 2


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_real_ffmpeg_checks_run():
    assert doctor.check_binary("ffmpeg")["ok"] and doctor.check_binary("ffprobe")["ok"]
    assert doctor.check_subtitles_filter(Config())["name"] == "ffmpeg_subtitles"
