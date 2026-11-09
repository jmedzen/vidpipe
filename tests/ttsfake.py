"""測試用的假 TTS：回 24kHz 單聲道 WAV（前後各有靜音），跟 audio.cpp 的格式一樣。"""

from __future__ import annotations

import io
import json
import math
import struct
import threading
import time
import wave

RATE = 24000


def make_wav(lead=0.3, speech=0.5, tail=0.4, freq=440.0) -> bytes:
    """前後各有一段靜音、中間是音調的 WAV（24kHz 單聲道 16-bit，跟 audio.cpp 一樣）。"""
    frames = bytearray()
    frames += b"\x00\x00" * int(RATE * lead)
    for n in range(int(RATE * speech)):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * n / RATE)))
    frames += b"\x00\x00" * int(RATE * tail)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class FakeTTS:
    """記錄每次請求；可以指定哪些句子要失敗、或回靜音。"""

    def __init__(self, fake_server, *, fail_on=(), silent_on=(), delay=0.0, speech=None):
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self.fail_on, self.silent_on, self.delay = set(fail_on), set(silent_on), delay
        self.speech = speech  # 句子 → 聲音秒數；不給就固定 0.5 秒
        fake_server.handler = self.handle

    def handle(self, method, path, headers, body):
        if path == "/health":                      # 設定頁的「測試連線」與 doctor 會問
            return 200, {"status": "ok"}
        if path == "/v1/models":
            return 200, {"data": [{"id": "fake-tts"}]}
        if path != "/v1/audio/speech":
            return 404, {"error": "no route"}
        req = json.loads(body)
        with self.lock:
            self.calls.append(req)
        if self.delay:
            time.sleep(self.delay)
        if req["input"] in self.fail_on:
            return 500, {"error": {"message": "boom", "code": "tts_failed"}}
        if req["input"] in self.silent_on:
            return 200, make_wav(lead=0.5, speech=0.0, tail=0.5)
        return 200, make_wav(speech=self.speech(req["input"]) if self.speech else 0.5)

    @property
    def texts(self):
        return [c["input"] for c in self.calls]
