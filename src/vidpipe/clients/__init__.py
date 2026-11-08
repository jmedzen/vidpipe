"""外部服務的 client：vidgrab（素材）與 audio.cpp／ttsgrab（旁白）。"""

from .http import HttpClient
from .tts import TTSClient
from .vidgrab import VidgrabClient

__all__ = ["HttpClient", "TTSClient", "VidgrabClient"]
