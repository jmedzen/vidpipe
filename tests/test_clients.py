from __future__ import annotations

import pytest

from vidpipe.clients import HttpClient, TTSClient, VidgrabClient
from vidpipe.errors import BadInput, UpstreamError

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def _no_sleep(_s):
    return None


def test_structured_vidgrab_error_is_preserved(fake_server):
    fake_server.handler = lambda *_: (400, {"ok": False, "error": {
        "code": "bad_input", "message": "區段寫錯", "retryable": False, "hint": "用 1:00-2:30"}})
    c = HttpClient(fake_server.url, name="vidgrab", sleep=_no_sleep)
    with pytest.raises(UpstreamError) as info:
        c.get_json("/x")
    e = info.value
    assert e.upstream == "vidgrab" and e.upstream_code == "bad_input"
    assert e.retryable is False and e.hint == "用 1:00-2:30"
    assert len(fake_server.calls) == 1  # 不可重試就不重試
    d = e.to_dict()["error"]
    assert d["upstream"] == "vidgrab" and d["upstream_code"] == "bad_input"


def test_5xx_is_retried_then_succeeds(fake_server):
    seq = iter([(503, {"error": "busy"}), (503, {"error": "busy"}), (200, {"ok": True})])
    fake_server.handler = lambda *_: next(seq)
    c = HttpClient(fake_server.url, name="tts", retries=2, sleep=_no_sleep)
    assert c.get_json("/x") == {"ok": True}
    assert len(fake_server.calls) == 3


def test_5xx_gives_up_after_retries(fake_server):
    fake_server.handler = lambda *_: (500, {"error": "boom"})
    c = HttpClient(fake_server.url, name="tts", retries=1, sleep=_no_sleep)
    with pytest.raises(UpstreamError) as info:
        c.get_json("/x")
    assert info.value.retryable is True and "500" in info.value.message
    assert len(fake_server.calls) == 2


def test_404_not_retried_and_text_body_summarised(fake_server):
    fake_server.handler = lambda *_: (404, b"nope")
    c = HttpClient(fake_server.url, name="vidgrab", retries=3, sleep=_no_sleep)
    with pytest.raises(UpstreamError) as info:
        c.get_json("/x")
    assert info.value.retryable is False and len(fake_server.calls) == 1


def test_connection_refused_is_retryable_with_hint():
    c = HttpClient("http://127.0.0.1:1", name="vidgrab", retries=1, sleep=_no_sleep)
    with pytest.raises(UpstreamError) as info:
        c.get_json("/x")
    assert info.value.retryable is True and "連不上" in info.value.message
    assert "vidgrab_url" in info.value.hint


def test_auth_failure_hint_and_token_not_leaked(fake_server):
    fake_server.handler = lambda *_: (401, {"ok": False, "error": {
        "code": "unauthorized", "message": "沒帶 token", "retryable": False}})
    c = VidgrabClient(fake_server.url, "s3cret-token", sleep=_no_sleep)
    with pytest.raises(UpstreamError) as info:
        c.check_auth()
    assert info.value.upstream_code == "unauthorized"
    assert "s3cret-token" not in str(info.value) and "s3cret-token" not in info.value.hint
    assert fake_server.calls[0][2]["authorization"] == "Bearer s3cret-token"


def test_non_json_success_is_an_error(fake_server):
    fake_server.handler = lambda *_: (200, b"<html>")
    c = HttpClient(fake_server.url, name="vidgrab", sleep=_no_sleep)
    with pytest.raises(UpstreamError, match="不是 JSON"):
        c.get_json("/x")


def test_tts_speak_sends_voice_ref_and_returns_wav(fake_server):
    fake_server.handler = lambda *_: (200, WAV)
    c = TTSClient(fake_server.url, model="cosy", voice_ref="/ref.wav",
                  reference_text="參考句", sleep=_no_sleep)
    assert c.speak("你好世界") == WAV
    import json

    method, path, _h, body = fake_server.calls[0]
    assert (method, path) == ("POST", "/v1/audio/speech")
    assert json.loads(body) == {"input": "你好世界", "response_format": "wav", "model": "cosy",
                                "voice_ref": "/ref.wav", "reference_text": "參考句"}


def test_tts_speak_omits_unset_fields(fake_server):
    import json

    fake_server.handler = lambda *_: (200, WAV)
    TTSClient(fake_server.url, sleep=_no_sleep).speak("hi")
    assert json.loads(fake_server.calls[0][3]) == {"input": "hi", "response_format": "wav"}


def test_tts_speak_rejects_empty_text():
    with pytest.raises(BadInput):
        TTSClient("http://127.0.0.1:1").speak("   ")


def test_tts_speak_rejects_non_wav(fake_server):
    fake_server.handler = lambda *_: (200, b'{"error":"model not loaded"}')
    with pytest.raises(UpstreamError, match="不是 WAV"):
        TTSClient(fake_server.url, sleep=_no_sleep).speak("hi")


def test_tts_models_accepts_both_shapes(fake_server):
    fake_server.handler = lambda *_: (200, {"data": [{"id": "cosy"}]})
    assert TTSClient(fake_server.url).models() == [{"id": "cosy"}]
    fake_server.handler = lambda *_: (200, [{"id": "a"}, "junk"])
    assert TTSClient(fake_server.url).models() == [{"id": "a"}]
