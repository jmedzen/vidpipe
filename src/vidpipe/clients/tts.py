"""旁白 client：audio.cpp 或任何 OpenAI 風格的 `/v1/audio/speech`（例如 ttsgrab）。

audio.cpp 的行為（實測）：
- 模型要先 load 才能用（`POST /v1/models/load`，WebUI 也行）；剛啟動時 `/v1/models` 是空的。
- CosyVoice3 一定要 `voice_ref`（參考音檔路徑，是 **audio.cpp 那台機器上的路徑**）
  加 `reference_text`（逐字稿），不然回 500。
- 預設回 WAV：單聲道、24kHz、16-bit。
"""

from __future__ import annotations

from typing import Any

from ..errors import BadInput, UpstreamError
from .http import HttpClient


class TTSClient(HttpClient):
    def __init__(self, base_url: str, token: str = "", *, model: str = "",
                 voice_ref: str = "", reference_text: str = "", **kw: Any):
        kw.setdefault("timeout", 120.0)  # 合成比一般請求久
        super().__init__(base_url, name="tts", token=token, **kw)
        self.model = model
        self.voice_ref = voice_ref
        self.reference_text = reference_text

    def health(self) -> dict[str, Any]:
        return self.get_json("/health", timeout=10)

    def models(self) -> list[dict[str, Any]]:
        """已載入的模型。回應形狀兩種都接：OpenAI 的 {"data": [...]} 或直接是清單。"""
        data = self.get_json("/v1/models", timeout=10)
        if isinstance(data, dict):
            data = data.get("data") or data.get("models") or []
        return [m for m in data if isinstance(m, dict)]

    def transcribe(self, path: str, *, model: str, language: str = "") -> str:
        """audio.cpp 的 ASR：辨識 TTS 伺服器那台機器上的音檔，回文字。

        `path` 是那台機器上的路徑（audio.cpp 自己讀檔）。"""
        payload: dict[str, Any] = {"model": model, "audio": path}
        if language:
            payload["language"] = language
        data = self.request("POST", "/v1/audio/transcriptions", json=payload).json()
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise UpstreamError("ASR 回的不是 {\"text\": …}", upstream="tts", retryable=False,
                                hint="確認 asr_model 是 audio.cpp 已載入的 ASR 模型")
        return data["text"]

    def speak(self, text: str, *, model: str = "", voice_ref: str = "",
              reference_text: str = "") -> bytes:
        """合成一段文字，回傳 WAV 位元組。"""
        text = (text or "").strip()
        if not text:
            raise BadInput("旁白文字是空的", hint="每個 scene 的 text 都要有內容")
        payload: dict[str, Any] = {"input": text, "response_format": "wav"}
        use_model = model or self.model
        if use_model:
            payload["model"] = use_model
        ref = voice_ref or self.voice_ref
        if ref:
            payload["voice_ref"] = ref
            ref_text = reference_text or self.reference_text
            if ref_text:
                payload["reference_text"] = ref_text
        resp = self.request("POST", "/v1/audio/speech", json=payload)
        body = resp.content
        if not body.startswith(b"RIFF"):
            raise UpstreamError(
                "tts 回的不是 WAV（可能是 JSON 錯誤或別的格式）", upstream="tts",
                retryable=False,
                hint="確認伺服器支援 response_format=wav；用 audio.cpp 時模型要先載入")
        return body
