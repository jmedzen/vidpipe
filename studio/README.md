# studio:從主題到可上傳的影片

vidpipe 負責「manifest → 成片」。`studio/` 是它的上游與周邊:選題、腳本與 hook、佛法審稿、挑素材、
包裝(標題、描述、標籤)與自動品質檢查。成片一律呼叫 vidpipe,素材向 vidgrab 要,旁白由 audio.cpp 合成。

`studio/` 不進 Docker 映像,也不影響 `vidpipe serve`;它是在開發機上跑的工具。

```
topics/topics.json ─→ brief ─→ 腳本(LLM)─→ 規則檢查 ─→ 審稿(LLM,獨立)─→ 挑素材(vidgrab)
      ─→ manifest ─→ vidpipe make ─→ 包裝(LLM)─→ 品質檢查 ─→ out/<日期>_<主題>_<格式>/
```

## 先決條件

1. 在 repo 根目錄 `pip install -e ".[dev]"`(`studio/pipeline.json` 預設用 `../.venv/bin/vidpipe`)。
2. vidgrab 在跑(`http://127.0.0.1:8310`),audio.cpp 在跑而且**已載入模型**(`http://127.0.0.1:8301/health`
   的 `models` 大於 0)。Mac 上要用有 libass 的 ffmpeg(`pipeline.json` 的 `path_prepend`)。
3. `python3 -m unittest discover -s studio/tests`(或在根目錄跑 `pytest`)確認環境。測試不連網路。

## 用法

```bash
cd studio
./run run --topic letting-go --seconds 45          # 一支直式 Shorts(30–120 秒)
./run run --topic impermanence --format long       # 一支橫式長影片(5–12 分鐘)
./run batch --count 5 --name week46                # 批次;長度約 4:4:2 分布在 30–60、60–90、90–120 秒
./run status                                       # 每個 run 做到哪、在等哪個 LLM 回應
```

- 每個階段的輸入輸出都是 `work/<run_id>/` 底下的檔案,做完的階段不重做。**失敗或中斷後,重跑同一個指令就會接著做**
  (批次用同一個 `--name`)。旁白與已渲染的場景在 vidpipe 那邊也有快取。
- 結束碼:`0` 完成、`3` 在等 LLM 回應(見下)、`1` 被退回或失敗(原因在輸出的 JSON 與 `work/<run_id>/run.log`)。
- 成品在 `out/<日期>_<主題>_<格式>/`:`video.mp4`、`cover.jpg`、`description.txt`、`credits.md`、`subtitles.srt`、
  `metadata.json`(標題候選、標籤、置頂留言、出處、審稿分數)、`qc.json`、`sheet.jpg`(影格總覽)、`script.json`、`manifest.json`。
- **不會上傳到任何平台。**

## LLM 後端

用環境變數 `PIPELINE_LLM` 選(可寫在 `studio/.env`,不進版控):

| 值 | 說明 |
|---|---|
| `files`(預設) | 不呼叫模型。需要 LLM 時把提示詞寫到 `work/<run_id>/<階段>.prompt.md`,以結束碼 3 停下;把 JSON 回應寫到 `<階段>.response.json` 之後重跑同一個指令。測試階段由代理(Sonnet)讀提示詞、寫回應 |
| `openai` | OpenAI 相容端點(llama-server、LM Studio…):`PIPELINE_LLM_URL`、`PIPELINE_LLM_MODEL`、`PIPELINE_LLM_KEY` |
| `anthropic` | Anthropic API:`ANTHROPIC_API_KEY`、`PIPELINE_LLM_MODEL` |
| `claude-cli` | 本機的 `claude -p` |

換成後三者之一,`./run run …` 就是一個指令從主題到成品、不需人工介入。提示詞全部在 `prompts/`。

## 設定:`pipeline.json`

| 項目 | 意思 |
|---|---|
| `vidpipe.bin`、`vidpipe.env` | vidpipe 的執行檔與環境變數(TTS、vidgrab 位址、字幕字體) |
| `voice` | TTS 模型 id、音色參考音檔與逐字稿(路徑是 audio.cpp 那台機器上的) |
| `timing` | 旁白的停頓(場景開頭、句間、場景結尾) |
| `series` | 畫面下方的系列名稱 |
| `visuals.allow_unknown_license` | 是否放行 YouTube 上授權未標示的素材(授權明確的永遠優先) |
| `music.tracks` | 配樂候選(vidgrab 音樂庫的 asset id,CC BY);每支片依 run id 固定挑一首 |
| `chars_per_second` | 腳本字數換算秒數(4.2) |
| `review_max_rounds` | 腳本最多改幾輪 |

`PIPELINE_WORK`、`PIPELINE_OUT` 可以把中間檔與成品放到別的地方。

## 各階段

- **選題**:`topics/topics.json`(主題、核心觀念、允許的出處、日常場景);做過的記在 `topics/_done.json`,批次會跳過。
- **腳本**:`prompts/script.md`。產出 hook 備選、常駐標題、場景(每景一個畫面主題)、金句、小練習、出處。
- **規則檢查**(`pipeline/textcheck.py`,不靠 LLM):總字數、句長 20 字、禁用的開場與用語、簡體字、出處與標題。
- **審稿**:`prompts/review.md`,由另一個獨立的 LLM 呼叫執行。硬性問題或任何一項低於 4 分就退回重寫;
  通過但意見超過兩條會再磨一輪;改到最後一輪仍有佛法疑義就停下來交給人(`DOUBTS.json`)。
- **挑素材**(`pipeline/footage.py`、`assets/footage.json`):每個畫面主題維護一個 vidgrab 素材池。授權明確的優先,
  同一支片盡量不重複,相鄰鏡頭不用同一段。場景依句子切成「鏡頭」,約 4 到 6 秒換一次畫面。
- **成片**:組出 manifest(常駐標題與系列名稱用 `overlays`、配樂、`fit: crop`),呼叫 `vidpipe validate` 與 `vidpipe make`。
- **包裝**:`prompts/package.md` 產出標題候選、摘要、標籤、置頂留言;描述另外併入出處、配樂與畫面素材的標註。
- **品質檢查**(`pipeline/qc.py`):解析度、編碼、幀率、長度範圍、響度(±1.5 LU)與真峰值、靜音、黑畫面、凍結、
  字幕與旁白起點誤差(< 0.3 秒)、字幕與腳本逐字相同、簡體字、授權、CC BY 標註是否進了描述。硬性項目不過就不出貨。

## 文件

- [docs/resources.md](docs/resources.md):vidgrab、vidpipe、audio.cpp 的盤點與健康檢查
- [docs/style-guide.md](docs/style-guide.md):風格指南(參考頻道的形式分析與本專案的規格)
- [docs/design.md](docs/design.md):缺口清單與設計
- [docs/iteration-log.md](docs/iteration-log.md):每一輪的成品分數、問題與改動,以及 PR 的 CI 紀錄
