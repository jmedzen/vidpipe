# 可用資源盤點

實測日期:2026-11-10(MacBook Pro 192.168.2.66,該機當天 10:22 重開機過)。
開工時要重跑「健康檢查」一節,不要直接相信這份文件裡的版本號。

## 總覽

| 服務 | 位置 | 實測狀態 | 用途 |
|---|---|---|---|
| vidgrab(本機) | `http://127.0.0.1:8310`,`--no-auth` | 正常,build `73.6e800a8` | 搜尋與下載素材、音樂,附授權資訊 |
| vidgrab(NAS Docker) | `http://192.168.2.7:8310` | 正常,build `71.d339695` | 同上,線上版 |
| vidpipe(本機) | `http://127.0.0.1:8320`,`--no-auth` | 正常,build `54.557eb89` | manifest → 旁白 → 字幕 → 成片 |
| vidpipe(NAS Docker) | `http://192.168.2.7:8320`(8311 沒開) | 正常,build `61.0c528c2` | 同上,線上版 |
| audio.cpp | `http://127.0.0.1:8301`(聽 `*:8301`) | 起來了,但**沒有載入模型**(`models: 0`) | 本機 TTS,只使用不修改 |

工作副本(所有開發都在這裡,原目錄只讀):

- `_repos/vidgrab`:`git worktree`,分支 `vidoutput/base`,追蹤 `origin/main`(6e800a8)。
- `_repos/vidpipe`:從 GitHub clone(0c528c2)。

## vidgrab

素材庫。三種介面行為一致:CLI(stdout 只有 JSON)、REST(`/docs`、`/api/openapi.json`)、MCP(stdio 與 `POST /mcp`)。

- 來源:`bilibili`、`youtube`(`both`)、`pexels`、`pixabay`(`stock`)、`openverse`、`jamendo`(`music`)。
- 主要指令:`search`、`pick`、`batch`(冪等、可設 timeout)、`vault`(素材庫快照)、`find`、`refresh`、`test`。
- 主要端點:`POST /api/search`(回 `job`)、`GET /api/job/{id}`、`POST /api/batch`、`GET /api/vault`、`GET /api/find/{id}`、`POST /api/frame`、`GET /file/...`、`GET /api/version`。
- 穩定 id:`pixabay:123`、片段 `pixabay:123#2-8`、整支 `#full`。
- 錯誤格式:`{"ok": false, "error": {code, message, retryable, hint}}`。
- 授權分類:`free`、`free-attribution`、`check`、`unknown`。只有前兩種可用於成品。
- 音樂庫只收 CC0、公共領域、CC BY。

已知限制:

- **本機與 NAS 都沒有 Pixabay 或 Pexels API key**,`platform=stock` 直接回「素材站都不能用」。需要使用者提供 `PIXABAY_API_KEY`(Pexels 目前暫停新申請)。
- Jamendo 沒有 client id。Openverse 匿名可用,但實測「calm ocean」回 0 支,結果很薄,要多試幾個關鍵字。
- 沒有「指定網址下載」的指令,只能用關鍵字搜尋。
- B 站與 YouTube 的素材授權多半是 `unknown`,預設不用於成品。
- 支援 Python 3.9,不可使用 3.10 以上才有的語法。

本地測試:`./vidgrab test` → 251 passed、前端一致、Dockerfile 檢查通過。
(第一次要先 `uv pip install -p .venv/bin/python -e ".[dev]"`,launcher 自己建的 venv 沒有 pytest。)

## vidpipe

manifest 驅動的成片流水線。CLI、HTTP API(`/api/*`)、MCP、網頁介面。細節見 repo 的 `README.md`、`AGENTS.md`、`docs/agent-recipe.md`。

- 指令:`validate`(不呼叫 TTS,回分句、字數、粗估秒數)、`plan`(用 `query` 向 vidgrab 挑素材)、`narrate`(逐句 TTS,時間軸取自實際音檔)、`render`、`make`(= narrate + render)、`cover`、`credits`、`doctor`,以及 `normalize`/`trim`/`fill`/`concat`/`probe`。
- manifest:`schema: vidpipe.manifest/1`,必填 `id`、`scenes`。場景給 `text` 或 `sentences`,素材給 `clip.asset_id` 或 `clip.path`,沒有旁白的場景給 `min_duration`。不認得的欄位一律報錯。
- 輸出:`output.aspect` 支援 `9:16`、`1:1`、`16:9`;`fit` 支援 `blur_pad`、`crop`、`letterbox`。
- 字幕:`zh-TW` 或 `zh-TW+en`,樣式目前只有 `bold-bottom`,每行約 14 個中文字、最多兩行。
- 音樂:`music.path` 或 `music.asset_id`,`volume`、`duck_under_voice`;成片標準化到 `target_lufs`(預設 -14)。
- 產物:`out/<id>.mp4`、`<id>.cover.jpg`、`<id>.credits.md`;中間檔在 `work/shorts/<id>/`(`timeline.json`、`narration.wav`、`subs.srt`、`subs.ass`、`sources.json`)。
- 長工作有 job 機制:`POST /api/make` 回 202 與 `job`,`GET /api/job/{id}` 查進度。同一支片同時只能有一個工作(409 `conflict`)。
- 設定優先序:`VIDPIPE_<NAME>` 環境變數 > `vidpipe.json` > 預設。

已知限制:

- 總長超過 60 秒會警告,超過 3 分鐘不算 Shorts。長片(5 到 12 分鐘、16:9)在 schema 上可行,但尚未實測。
- 字幕樣式只有一種,沒有「常駐標題」、「品牌字」這類疊加層,也沒有轉場與字幕動態。
- `mute_source: false` 尚未實作,素材原音一律靜音。
- 本機設定的 `tts_model`、`voice_ref`、`reference_text` 都是空的,音色要由 manifest 的 `voice` 或環境變數指定。
- Mac 上必須用 `~/.local/bin/ffmpeg`(有 libass),並設 `VIDPIPE_SUBTITLE_FONT="PingFang TC"`。

本地測試(`_repos/vidpipe`,Python 3.12):`ruff check .` 通過;`pytest -q` 466 passed、1 failed。
失敗的是 `tests/test_settings.py::test_font_check_accepts_an_installed_cjk_font`:這台 Mac 的 fontconfig 把測試挑的字體解析成「宗楷體 W7」的另一個名稱,libass 回報的名稱與預期不同。屬於本機環境差異,CI(Linux、Noto CJK)不受影響。

## audio.cpp

第三方工具,位置 `~/audio.cpp`,由 `start-server-8301.command` 啟動(`--ui --ui-management --backend metal --host 0.0.0.0 --port 8301`)。**只使用,不修改。**

- `GET /health`:`{"status":"ok","models":N,...}`。`GET /v1/models`:已載入的模型。
- `POST /v1/models/load`:`{"id","path","family","task"}` 或 `{"id","package_id","task"}`。重啟後模型要重新載入。
- `POST /v1/audio/speech`:`{"model","input","response_format":"wav","voice_ref","reference_text","options":{...}}`,預設回 24 kHz 單聲道 16-bit WAV。
- 已安裝的模型(`~/audio.cpp/bin/models/`):`Drama-3-GGUF`(Fish Audio Drama 3,family `fish_drama3`)、`Fish-Audio-S2-Pro-GGUF`(`fish_audio`)、`CosyVoice3-GGUF`、`IndexTTS2-GGUF`、`Qwen3-TTS-12Hz-1.7B-Base-GGUF`、`Sidon-GGUF`。
- 使用者指定:TTS 用 Fish Audio Drama 3,音色參考音檔在 `/Users/jm/SyncDev/23-ref Audio/`(`Ref-Audio-馬克思1.wav` 到 `3.wav`,各約 10 到 12 秒,24 kHz 單聲道)。該資料夾只讀。

已知限制:

- 剛啟動時沒有載入任何模型,`vidpipe doctor` 的 `tts` 檢查會失敗。
- `voice_ref` 是 audio.cpp 那台機器上的路徑。NAS 上的 vidpipe 容器要連 Mac 的 8301,路徑仍是 Mac 的。
- 參考音檔沒有附逐字稿,需要自己轉寫後填 `reference_text`。

## LLM(腳本生成與審稿)

- 本機 `claude -p` 回「Credit balance is too low」,目前不能用。
- 沒有 `ANTHROPIC_API_KEY`。
- 本機有 `llama-server` 與 `~/models-LLM/` 的 GGUF 模型,可起一個 OpenAI 相容端點。
- 流水線的 LLM 後端做成可抽換(OpenAI 相容端點、Anthropic API、`claude -p`),用環境變數選。

## 健康檢查

```bash
curl -s http://127.0.0.1:8301/health            # audio.cpp,models 要大於 0
curl -s http://127.0.0.1:8310/api/version       # vidgrab 本機
curl -s http://127.0.0.1:8320/api/version       # vidpipe 本機
curl -s http://192.168.2.7:8310/api/version     # vidgrab 線上
curl -s http://192.168.2.7:8320/api/version     # vidpipe 線上
curl -s http://127.0.0.1:8320/api/doctor        # ffmpeg、字體、vidgrab、TTS 一次看
```

## 部署機制

merge 進 main → `publish.yml` 建 `latest` 映像推到 ghcr.io → NAS 的 watchtower 每 30 秒拉取更新。
進入 main 等於上線,所有驗證要在 merge 前完成。規則見使用者規格第四節,紀錄寫在 `docs/iteration-log.md`。
