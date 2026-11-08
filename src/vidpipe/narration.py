"""逐句合成旁白：句子 → TTS → 修掉頭尾靜音 → 量時長（字幕時間軸的原料）。

- **快取**：同樣的（文字、模型、音色參考、逐字稿）不重複合成，檔名就是內容雜湊。
  改一個字只重做那一句；換模型或音色整批重做。
- **修靜音**：TTS 的頭尾常有一小段空白，不修掉每一句之間就會有莫名的停頓。頭尾各留 50 毫秒。
- **時長**：直接讀 WAV 標頭（標準庫 `wave`），不靠 ASR，精準而且確定。
- **並行度**可設（`tts_concurrency`）；結果順序永遠跟輸入一致，相同的句子只合成一次。
- 取消（SIGTERM）或任何一句失敗，尚未開始的句子不再送出；已完成的留在快取裡，重跑只補缺的。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import wave
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg, jobs
from .clients import TTSClient
from .config import Config
from .errors import BadInput, UpstreamError

# 修靜音的參數改了，舊快取就不能用：把版本放進雜湊
TRIM_VERSION = "silence-v1"
_TRIM_FILTER = (
    "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:start_silence=0.05,"
    "areverse,"
    "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB:start_silence=0.05,"
    "areverse"
)
MIN_SPEECH_SECONDS = 0.05


@dataclass(frozen=True)
class Clip:
    index: int
    text: str
    path: str
    duration: float
    cached: bool

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "text": self.text, "path": self.path,
                "duration": self.duration, "cached": self.cached}


def cache_dir(cfg: Config) -> Path:
    return Path(cfg.work_dir) / "cache" / "tts"


def cache_key(text: str, cfg: Config) -> str:
    """同一句、同一組設定 → 同一個 key。換服務位址不影響（同一個模型同一個音色就是同一份音檔）。"""
    payload = json.dumps({"text": text, "model": cfg.tts_model, "voice_ref": cfg.voice_ref,
                          "reference_text": cfg.reference_text, "trim": TRIM_VERSION},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def wav_duration(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (wave.Error, EOFError, OSError) as exc:
        raise UpstreamError(f"讀不了 TTS 回的音檔：{exc}", upstream="tts", retryable=False,
                            hint="確認 TTS 回的是 PCM WAV（response_format=wav）") from exc


def _client(cfg: Config) -> TTSClient:
    return TTSClient(cfg.tts_base_url, cfg.tts_token, model=cfg.tts_model,
                     voice_ref=cfg.voice_ref, reference_text=cfg.reference_text)


def _synthesize_one(text: str, cfg: Config, target: Path,
                    make_client: Callable[[Config], TTSClient]) -> None:
    """合成一句、修靜音，原子地寫成 target。"""
    # 暫存檔名帶隨機字尾：同一句可能同時被另一個（或前一次失敗而還在收尾的）行程處理，不能共用檔名
    token = secrets.token_hex(4)
    raw = target.with_name(f".{target.stem}.{token}.raw.wav")
    partial = target.with_name(f".{target.stem}.{token}.partial.wav")
    try:
        raw.write_bytes(make_client(cfg).speak(text))
        ffmpeg.run(["-i", str(raw), "-af", _TRIM_FILTER, "-c:a", "pcm_s16le", str(partial)])
        if wav_duration(partial) < MIN_SPEECH_SECONDS:
            raise UpstreamError(
                f"TTS 對這句回的幾乎是靜音：{text[:20]}", upstream="tts", retryable=True,
                hint="重試，或換個說法（太短、只有標點的句子容易這樣）")
        partial.replace(target)
    finally:
        raw.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def synthesize(sentences: Sequence[str], cfg: Config, *, concurrency: int | None = None,
               make_client: Callable[[Config], TTSClient] = _client) -> list[Clip]:
    """逐句合成，回傳跟輸入同順序的 Clip。"""
    texts = [s.strip() for s in sentences]
    if not texts or any(not t for t in texts):
        raise BadInput("沒有可以合成的句子",
                       hint="每一句都要有內容；先用 `vidpipe split` 看分句結果")
    workers = max(1, concurrency or cfg.tts_concurrency)
    root = cache_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)

    keys = [cache_key(t, cfg) for t in texts]
    todo: dict[str, str] = {}  # 相同的句子只合成一次
    for key, text in zip(keys, texts, strict=True):
        if not (root / f"{key}.wav").is_file() and key not in todo:
            todo[key] = text
    cached_keys = {k for k in keys if k not in todo}

    jobs.stage("narrate", f"{len(todo)} 句要合成，{len(set(keys)) - len(todo)} 句用快取")
    done = 0
    total = len(todo)
    if total:
        stop = threading.Event()  # 任何一句失敗（或被取消）後，還沒開始的句子不再送出

        def task(key: str, text: str) -> None:
            if stop.is_set():
                return
            try:
                _synthesize_one(text, cfg, root / f"{key}.wav", make_client)
            except BaseException:
                stop.set()  # 在這個 worker 接下一件工作之前就設好
                raise

        pool = ThreadPoolExecutor(max_workers=workers)
        futures: dict[str, Future[None]] = {
            key: pool.submit(task, key, text) for key, text in todo.items()}
        try:
            for key, fut in futures.items():  # 依序等；第一個錯誤就停
                fut.result()
                done += 1
                jobs.progress(done / total)
                jobs.log(f"合成完成 {done}/{total}：{todo[key][:24]}")
        except BaseException:  # 失敗、取消（SIGTERM → Cancelled）、Ctrl-C
            stop.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

    clips: list[Clip] = []
    for i, (key, text) in enumerate(zip(keys, texts, strict=True)):
        path = root / f"{key}.wav"
        clips.append(Clip(i, text, str(path), round(wav_duration(path), 3), key in cached_keys))
    return clips
