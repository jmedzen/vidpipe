> 2026-11-10 更新:流水線原本放在獨立的 `vidoutput` repo,依使用者決定搬進 vidpipe 的 `studio/`。
> 下文的「vidoutput」即 `studio/`。另外兩點與最初設計不同:素材改用 vidgrab 的 YouTube 來源(每個畫面主題一個素材池,
> 不逐場景呼叫 `vidpipe plan`,因為用旁白關鍵字搜 YouTube 會搜到談話節目);沒有做「自行生成背景」的後備路徑。

# 缺口清單與流水線設計

## 一、缺口

vidpipe 與 vidgrab 已經涵蓋「manifest → 旁白 → 字幕 → 素材 → 成片 → 封面 → credits」。缺的是上游與周邊:

| # | 缺口 | 放在哪裡 | 做法 |
|---|---|---|---|
| 1 | 選題與去重 | vidoutput | `topics/*.json` 主題庫,`topics/_done.json` 記錄做過的 |
| 2 | 腳本與 hook 生成 | vidoutput | `prompts/script.md`,LLM 產出 `script.json`(含 3 到 5 個 hook 備選) |
| 3 | 佛法審稿 | vidoutput | `prompts/review.md`,獨立的 pass 產出 `review.json`,不過就退回重寫,最多 3 輪 |
| 4 | 腳本規則檢查 | vidoutput | 程式檢查字數、句長、禁用語、出處欄位,不靠 LLM |
| 5 | 沒有素材站 key 時的畫面 | vidoutput | `pipeline/visuals.py` 自行生成原創背景動畫(Pillow 加 ffmpeg) |
| 6 | 自製畫面在 credits 被標成授權不明 | **vidpipe** | manifest 的 `clip` 加選填的 `license` 與 `credit`,本機檔案可宣告授權 |
| 7 | 常駐標題與系列名稱疊字 | **vidpipe** | manifest 加選填的 `overlays`(文字、位置、樣式),燒字幕時一起畫 |
| 8 | 自動品質檢查 | vidoutput | `pipeline/qc.py`:規格、響度、靜音、黑畫面、字幕對時、授權、字體 |
| 9 | 上架包裝 | vidoutput | `prompts/package.md` 產出標題候選、描述、標籤、置頂留言,併入 credits |
| 10 | 批次與續跑 | vidoutput | `pipeline batch`,每個階段有完成標記,失敗重試,可從中斷處續跑 |
| 11 | 長影片 16:9 | vidoutput,必要時 vidpipe | 章節大綱 → 每章一組場景,`output.aspect: "16:9"`;先實測 vidpipe 的支援程度 |
| 12 | vidgrab 沒有不需 key 的授權明確影片來源 | vidgrab(候選) | 之後評估加 Wikimedia Commons 來源;先等 Pixabay key |

第 6、7 項是對 manifest 的新增欄位,舊 manifest 不受影響,走第四節的 PR 流程。在 PR 上線之前,流水線先用不含這兩個欄位的 manifest,功能以設定開關控制。

## 二、原則

- 能用 vidpipe 的就呼叫 vidpipe,不自己拼成片用的 ffmpeg 指令。自己寫 ffmpeg 的只有兩處:生成背景素材、品質檢查的量測。
- 每個階段的輸入輸出都是檔案,放在 `work/<run_id>/`。階段做完寫 `<stage>.done`,重跑時跳過。
- LLM 後端可抽換(見第四節)。提示詞全部在 `prompts/`。
- 只依賴 Python 標準庫與 Pillow,外部呼叫都有逾時與重試。

## 三、階段

```
topics/*.json
   │ 1 brief      選題 → brief.json(主題、核心觀念、出處、目標情緒、目標長度)
   │ 2 script     LLM → script.json(hook 備選、場景、每景的英文 query、金句)
   │ 3 lint       程式檢查(字數、句長、禁用語、出處)→ lint.json
   │ 4 review     LLM 獨立審稿 → review.json;不過 → 回到 2(帶審稿意見)
   │ 5 visuals    有素材站 → vidpipe plan;沒有 → 生成背景片段
   │ 6 manifest   組出 vidpipe manifest(voice、timing、overlays、music)
   │ 7 make       vidpipe validate → make(job 輪詢)→ mp4、cover、credits
   │ 8 package    標題候選、描述、標籤、置頂留言 → package.json、description.txt
   │ 9 qc         自動檢查 → qc.json;硬性失敗 → 回到對應階段
   ▼ 10 export    複製到 out/<日期>_<主題>/
```

`run_id` = `<日期>_<主題 id>_<格式>`,例如 `20261110_impermanence_short`。

### 腳本格式(`script.json`)

```json
{
  "topic": "impermanence", "format": "short", "target_seconds": 45,
  "hooks": ["……", "……", "……"], "hook": "……",
  "title": "常駐標題(20 字內)",
  "scenes": [
    {"role": "hook", "text": "……", "query": ["english keywords"], "mood": "calm"}
  ],
  "closing": "金句",
  "sources": [{"work": "《金剛經》", "note": "意譯", "quote": ""}],
  "practice": "小練習"
}
```

長影片多一層 `chapters`,每章有 `title` 與 `scenes`。

### 審稿(`review.json`)

`{"pass": bool, "scores": {...}, "issues": [{"severity": "hard|soft", "where", "problem", "fix"}]}`。
任何 `hard`(編造經文、概念誤用、勸誘或保證靈驗、替特定團體背書)都不通過。

### 畫面

- 優先:`vidpipe plan`(`platform: stock`,直式優先),逐條看 `report.warnings`,授權不是 `free`/`free-attribution` 的不用。
- 後備:`pipeline/visuals.py` 依場景的 `mood` 生成背景動畫(漸層、光暈、水墨圓相、緩慢推移),每個場景不同。全部原創,授權宣告為自製。
- 目前沒有任何素材站 key,所以實際走的是後備路徑。

### 品質檢查

每一項輸出 `{"name", "ok", "hard", "detail"}`:

- 規格:解析度、長寬比、幀率、編碼(H.264 yuv420p、AAC)、長度範圍。
- 聲音:整合響度(目標 ±1 LU)、真峰值、超過 1.5 秒的靜音段。
- 字幕:`subs.srt` 與 `timeline.json` 的起訖誤差小於 0.3 秒;每行字數不超過版面;抽影格確認字幕區有文字像素。
- 畫面:黑畫面偵測、凍結偵測、相鄰場景素材不重複。
- 授權:`credits` 的 `unclear` 為空;CC BY 的標註文字已出現在描述。
- 文字:字幕文字與腳本逐字相同,沒有簡體字(對照常見簡繁差異字表)。

## 四、LLM 後端

`pipeline/llm.py`,用 `PIPELINE_LLM` 選:

| 後端 | 說明 |
|---|---|
| `files`(預設) | 不呼叫任何模型。階段需要 LLM 時,把組好的提示詞寫到 `work/<run>/<stage>.prompt.md`,等 `<stage>.response.json` 出現。測試階段由 Sonnet(代理)讀提示詞、寫回應 |
| `openai` | 任何 OpenAI 相容端點(本機 llama-server、LM Studio):`PIPELINE_LLM_URL`、`PIPELINE_LLM_MODEL` |
| `anthropic` | Anthropic API:`ANTHROPIC_API_KEY` |
| `claude-cli` | `claude -p`(目前這台機器餘額不足,不能用) |

`files` 後端讓流水線在沒有任何 API key 的情況下仍然是「檔案進、檔案出」:缺回應時 `pipeline run` 以結束碼 3 停在該階段並說明等哪個檔,回應到位後重跑同一個指令就接著做。換成其他後端就是全自動。

## 五、指令

```bash
./run run --topic impermanence --format short --seconds 45
./run run --topic impermanence --format long
./run batch --count 5            # 依主題庫與長度分布挑 5 支
./run status                     # 每個 run 做到哪個階段
```

設定在 `pipeline.json`(可進版控)與 `.env`(不進版控)。

## 六、目錄

```
pipeline/    程式(cli、stages、llm、vidpipe_client、visuals、qc、package)
prompts/     提示詞
topics/      主題庫與已完成紀錄
docs/        文件與迭代日誌
tests/       單元測試與端對端冒煙測試(假的 vidpipe 伺服器)
assets/      字體設定、參考分析(reference/ 不進版控)
work/ out/   中間檔與成品(不進版控)
_repos/      vidgrab、vidpipe 的工作副本(不進版控)
```
