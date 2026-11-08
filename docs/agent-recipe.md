# Agent recipe：從一份腳本做出一支 Short

給 AI agent（Claude Code 之類）用的完整做法。**stdout 只有 JSON、錯誤有 `code`／`retryable`／`hint`**，
所以照著結構化的回應走就行，不需要讀人看的訊息。

三種接法，做的事一樣，挑你的 agent 方便的：

| 接法 | 怎麼接 | 適合 |
|---|---|---|
| MCP（stdio） | `claude mcp add vidpipe -- vidpipe mcp` | agent 與 vidpipe 在同一台機器 |
| MCP（HTTP） | `claude mcp add --transport http vidpipe http://localhost:8320/mcp --header "Authorization: Bearer $VIDPIPE_API_TOKEN"` | vidpipe 在 Docker 或別台機器 |
| CLI／HTTP | `vidpipe <指令>`；`POST /api/<指令>` | 腳本、CI、不支援 MCP 的 agent |

下面以 MCP 的工具名稱為主，括號裡是對應的 CLI／HTTP。

## 0. 先確認環境

`check_setup`（`vidpipe doctor`、`GET /api/doctor`）。三個東西要就緒：

- **ffmpeg**（含 `subtitles` 濾鏡與一個繁中字體；Mac 要設 `VIDPIPE_SUBTITLE_FONT`，例如 `PingFang TC`）
- **vidgrab**（找素材）：`VIDPIPE_VIDGRAB_URL`，要 token 時 `VIDPIPE_VIDGRAB_TOKEN`；Pexels／Pixabay 的 API key 設在 vidgrab 那邊
- **TTS**（audio.cpp 或任何 OpenAI 風格的 `/v1/audio/speech`）：`VIDPIPE_TTS_BASE_URL`

回的 `checks[]` 每項都有 `ok` 與 `hint`；`ok: false` 但 `required: false` 的只是提醒。

## 1. 寫草稿、讓 vidpipe 挑素材

草稿就是 manifest，只是場景可以用 `query`（英文關鍵字）代替 `clip`，`music` 也可以給 `query`：

```json
{"id": "taipei-night-01", "title": "台北夜市",
 "scenes": [
   {"text": "台北的夜晚，從一碗熱湯開始。蒸氣升起來，整條街都暖了。",
    "query": ["night market soup", "steaming bowl"]},
   {"text": "夜市的燈，一盞一盞亮起來。", "query": "city lights night"}],
 "music": {"query": "calm piano", "volume": 0.3}}
```

`plan_short`（`vidpipe plan draft.json -o shorts.json`、`POST /api/plan`）。純文字腳本也行：
空白行隔開場景，「關鍵字：a, b」那行是 query（見 `examples/taipei-night/script.txt`）。

回來的 `manifest` 是完整的、可以直接做；**但要看 `report`**：

- `warnings`：沒搜到、授權要確認、素材太短會循環、不是直式。**每一條都要處理或確認可以接受。**
- `scenes[].chosen` 與 `alternatives`：選了誰、為什麼、前 3 個備選。不滿意就把 manifest 裡那個場景的
  `clip.asset_id` 換成備選的 `asset_id`；或改 `query` 再 `plan_short` 一次。
- `without_clip`：沒挑到素材的場景。補一個 `query` 重跑，或自己指定 `clip`（`asset_id` 或伺服器上的 `path`）。
- 想放行授權不明的素材要明講（`search.allow_unknown_license: true`），而且成片的 `credits.md`
  會把它標出來；**公開發佈前自己確認**。預設只選 `free`（可自由使用）與 `free-attribution`（可用需標註）。

已經有素材了就跳過這步，自己寫 `clip`。`manifest_guide` 回完整的欄位說明與範例。

## 2. 檢查，再做

`validate_manifest`（`vidpipe validate`、`POST /api/validate`）：**不呼叫 TTS、不碰素材**，回每個場景的
分句、字數、粗估秒數。寫錯的欄位會帶路徑（例如 `scenes[1].txt`），照訊息改。Shorts 最穩的長度是
60 秒內；總長超過會有警告。

`make_short`（`vidpipe make`、`POST /api/make`）：旁白 → 時間軸 → 渲染 → 封面 → credits.md，一個工作。

- 會等最多 `wait` 秒（預設 60）；沒做完回 `done: false` 與 job id，之後 `get_job`（帶 `wait` 會等）。
  **不要重送 `make_short`**：同一支片同時只能有一個工作，會回 `conflict`。
- 想取消：`cancel_job`。已經合成的旁白與渲染好的場景會留在快取，下次接著做。
- 成功後回 `short`，`files[].url` 是成品的網址（接在 vidpipe 的位址後面，支援 `Range`）。
  同一台機器時工作結果裡的 `path` 也能直接用。

## 3. 調整：只重做需要的那一步

| 想改什麼 | 做什麼 | 花多少 |
|---|---|---|
| 換一個場景的素材、改音樂音量 | 改 manifest → `make_short` | 只重做改到的場景；旁白走快取 |
| 改旁白文字 | 改 manifest → `make_short` | 只重新合成改到的句子 |
| 封面換個畫面或標題 | `run_step` op=`cover`（`at`、`text`） | 幾秒，不重新渲染 |
| 素材來源清單 | `run_step` op=`credits` | 即時 |

常見的調整：

- 音樂太大或太小聲：`music.volume`（1 = 跟旁白一樣大聲、預設 0.3、0 = 關掉）。`duck_under_voice`
  讓旁白出聲時自動壓低音樂。
- 雙語字幕：`subtitles.langs: "zh-TW+en"`，並把每句寫成 `sentences: [{"text": …, "en": …}]`。
- 不要燒字幕：`subtitles.burn_in: false`，會在 mp4 旁邊放 `.srt`。
- 場景沒有旁白：給 `min_duration`（秒）。素材不夠長會循環並在 `warnings` 說。

## 4. 錯誤怎麼處理

每個錯誤都是 `{code, message, retryable, hint}`。先看 `retryable`，再看 `code`：

| code | 意思 | 做什麼 |
|---|---|---|
| `bad_input` | manifest 或參數寫錯 | 照 `message` 的欄位路徑與 `hint` 改，**不要原樣重試** |
| `not_found` | 沒有這支片、工作、素材或檔案 | `hint` 會說先做什麼（例如沒 narrate 就 render） |
| `conflict` | 這支片已經有工作在跑 | 等它（`get_job`）或取消，不要重送 |
| `upstream_error` | vidgrab 或 TTS 回錯或連不上 | `retryable` 為真就等一下重試；看 `upstream` 是哪一個、`upstream_code` 是對方的 code |
| `dependency_missing` | 缺 ffmpeg | 裝好再試 |
| `bad_config` | 設定值不合法（例如 `target_lufs`） | 改環境變數或 `vidpipe.json` |
| `render_failed`／`internal` | ffmpeg 失敗或沒預期的錯誤 | 重試一次；還是失敗就把 `message` 回報給人 |
| `cancelled` | 工作被取消 | 要的話重送 |

工作失敗時 `get_job` 的 `error` 也是同一個格式。

## 5. 本機驗收（在你的 Mac 上，真的 TTS 與素材）

雲端與 CI 連不到 audio.cpp 和素材站，所以 CI 的端到端驗收（`tests/test_acceptance.py`）用假 TTS 與合成素材，
**驗的是流水線，不是聲音和畫面好不好看**。第一次要自己跑一次、看一次、聽一次：

```bash
export VIDPIPE_SUBTITLE_FONT="PingFang TC"
export VIDPIPE_TTS_BASE_URL=http://127.0.0.1:8301        # audio.cpp 已載入模型
export VIDPIPE_VIDGRAB_URL=http://127.0.0.1:8310         # vidgrab 已設好 PIXABAY_API_KEY
vidpipe doctor
vidpipe plan examples/taipei-night/script.txt --id taipei-night-01 --title 台北夜市 \
  --music "calm piano" -o shorts.json                    # 看 report 的 warnings 與 chosen
vidpipe make shorts.json                                 # 成品在 out/taipei-night-01.mp4
```

看與聽的重點：

1. **旁白**：音色、斷句、語速。不對就改 `voice`（`voice_ref`／`reference_text`）或 `timing`，旁白會重新合成。
2. **字幕**：位置（避開底部約 15% 的 UI）、一行放不放得下、字體對不對。
3. **音樂**：旁白出聲時有沒有被蓋住、停頓時有沒有回來、結尾有沒有淡出。調 `music.volume`。
4. **素材**：每個場景的畫面跟旁白搭不搭、有沒有裁到重點（`focus: "0.5,0.3"` 可以調構圖中心、
   `fit` 可以換成 `crop`）；不搭就換 `asset_id`。
5. **credits.md**：每個素材的作者、授權、連結都在。要標註的（`free-attribution`）貼進影片說明。

## 不要做的事

- 不要自己拼 ffmpeg 指令：重構圖、編碼、混音、響度都在 vidpipe 裡，結果才會一致、可以快取。
- 不要把 API key、token 寫進 manifest 或回報；它們只在環境變數。
- 不要在 `plan` 之後直接當作定稿：至少看過 `warnings` 與每個場景選了什麼。
- 不要用 `clip.path` 指向不是你自己放的檔案：HTTP／MCP 收到的路徑是**伺服器上**的路徑。
