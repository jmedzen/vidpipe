"""時間軸相關測試共用的小工具。"""

from __future__ import annotations

import wave

from vidpipe import manifest, timeline
from vidpipe.narration import Clip

RATE = 24000


def parse(scenes, **top):
    return manifest.parse({"schema": "vidpipe.manifest/1", "id": "t1", "scenes": scenes, **top})


def clips_for(m, durations, directory=None):
    """依序給每一句一個長度的假 Clip（不碰 TTS）。"""
    texts = timeline.sentence_texts(m)
    assert len(texts) == len(durations)
    return [Clip(i, t, f"{directory or '/cache'}/{i}.wav", d, False)
            for i, (t, d) in enumerate(zip(texts, durations, strict=True))]


def tone(path, seconds, value=1000, rate=RATE, channels=1):
    """整段都是固定非零值的 WAV，方便檢查「這個位置有沒有聲音」。"""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(value.to_bytes(2, "little", signed=True) * channels * round(seconds * rate))
    return str(path)
