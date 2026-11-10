"""LLM 後端（可抽換）。所有後端都是「提示詞字串 → JSON 物件」。

PIPELINE_LLM = files（預設）｜openai｜anthropic｜claude-cli

files：不呼叫模型。把提示詞寫成 <stage>.prompt.md，等 <stage>.response.json 出現
（測試階段由代理讀提示詞、寫回應）。缺回應時丟 NeedsResponse，流水線以結束碼 3 停在這一步。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


class NeedsResponse(Exception):
    def __init__(self, prompt: Path, response: Path):
        super().__init__(f"等待 LLM 回應：讀 {prompt}，把 JSON 寫到 {response}")
        self.prompt, self.response = prompt, response


class LLMError(Exception):
    pass


def extract_json(text: str) -> dict:
    """從模型輸出取出第一個 JSON 物件（容忍 ```json 圍欄與前後說明文字）。"""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 回的不是合法 JSON：{exc}") from None
    if not isinstance(data, dict):
        raise LLMError("LLM 回的 JSON 不是物件")
    return data


def _post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(payload).encode(), {
        "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _call(backend: str, prompt: str, timeout: float) -> str:
    if backend == "openai":
        url = os.environ.get("PIPELINE_LLM_URL", "http://127.0.0.1:8080/v1").rstrip("/")
        key = os.environ.get("PIPELINE_LLM_KEY", "")
        data = _post(url + "/chat/completions", {
            "model": os.environ.get("PIPELINE_LLM_MODEL", "default"),
            "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
            {"Authorization": f"Bearer {key}"} if key else {}, timeout)
        return data["choices"][0]["message"]["content"]
    if backend == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise LLMError("沒有 ANTHROPIC_API_KEY（放進 .env）")
        data = _post("https://api.anthropic.com/v1/messages", {
            "model": os.environ.get("PIPELINE_LLM_MODEL", "claude-sonnet-5-5"),
            "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        return "".join(b.get("text", "") for b in data.get("content", []))
    if backend == "claude-cli":
        run = subprocess.run(["claude", "-p"], input=prompt, capture_output=True, text=True,
                             timeout=timeout, check=False)
        if run.returncode != 0:
            raise LLMError(f"claude -p 失敗：{(run.stdout or run.stderr).strip()[:200]}")
        return run.stdout
    raise LLMError(f"不認得的 PIPELINE_LLM：{backend}")


def ask(stage_dir: Path, name: str, prompt: str, *, retries: int = 2,
        timeout: float = 300) -> dict:
    """問 LLM。提示詞與回應都留檔（<name>.prompt.md／<name>.response.json）。"""
    backend = os.environ.get("PIPELINE_LLM", "files")
    prompt_file = stage_dir / f"{name}.prompt.md"
    response_file = stage_dir / f"{name}.response.json"
    if response_file.is_file():      # 已經有回應（續跑，或 files 後端的代理寫好了）
        return extract_json(response_file.read_text(encoding="utf-8"))
    prompt_file.write_text(prompt, encoding="utf-8")
    if backend == "files":
        raise NeedsResponse(prompt_file, response_file)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            data = extract_json(_call(backend, prompt, timeout))
            response_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
            return data
        except (LLMError, urllib.error.URLError, TimeoutError, KeyError,
                subprocess.TimeoutExpired) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise LLMError(f"LLM（{backend}）連續失敗：{last}")
