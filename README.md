# vidpipe

Shorts 流水線：**腳本 → 旁白與字幕 → 素材 → 9:16 成品**。

- 素材：[vidgrab](https://github.com/jmedzen/vidgrab)（B 站／YouTube／Pexels／Pixabay，帶授權資訊）
- 旁白：audio.cpp 本機 TTS（任何 OpenAI 風格的 `/v1/audio/speech` 都行，例如 ttsgrab）
- 渲染：ffmpeg（9:16 重構圖、統一編碼、字幕、混音）
- 給 AI agent 用：CLI（stdout 只有 JSON）、之後有 HTTP API 與 MCP
- 給人看：網頁介面（`vidpipe serve`），看三個服務的狀態、之後還有進度與成品預覽

> 狀態：旁白、時間軸、字幕、素材取得、渲染、背景音樂與響度都能用了（`narrate` → `render` 就得到一支成片）。目前有 `version`、`config`、`doctor`、`probe`、`normalize`、`trim`、`fill`、`concat`、`serve`、`jobs`／`job`／`cancel`、`split`、`speak`、`validate`、`narrate`、`render`；封面與 credits 在後續 PR。

## 快速開始

```bash
pip install -e ".[dev]"
vidpipe version
vidpipe config          # 目前生效的設定（token 會遮蔽）
vidpipe doctor          # 檢查 ffmpeg、vidgrab、TTS 是否都就緒
```

### Docker

```bash
docker build --build-arg VIDPIPE_BUILD="$(git rev-list --count HEAD).$(git rev-parse --short HEAD)" -t vidpipe .
docker run --rm vidpipe version
```

映像內有 ffmpeg 與 Noto CJK 字型（燒中文字幕用）。容器連不到主機的 `127.0.0.1`，
vidgrab 與 audio.cpp 的位址要用 `VIDPIPE_VIDGRAB_URL`、`VIDPIPE_TTS_BASE_URL` 指到
`host.docker.internal` 或實際 IP。

## 設定

優先序：環境變數 `VIDPIPE_<名稱>` > `vidpipe.json`（或 `VIDPIPE_CONFIG` 指定的檔）> 預設。

| 項目 | 預設 | 說明 |
|---|---|---|
| `vidgrab_url` / `vidgrab_token` | `http://127.0.0.1:8310` / 空 | 素材來源 |
| `tts_base_url` / `tts_token` | `http://127.0.0.1:8301` / 空 | 旁白（audio.cpp） |
| `tts_model` | 空 | TTS 模型 id；尚未決定，空 = 伺服器預設 |
| `tts_concurrency` | `1` | 同時送幾句去合成（1–16）；本機單一模型通常 1 最穩 |
| `voice_ref` / `reference_text` | 空 | 音色參考音檔與逐字稿（CosyVoice3 等需要） |
| `subtitle_langs` | `zh-TW` | `zh-TW` 或 `zh-TW+en`（雙語） |
| `burn_subtitles` | `true` | 字幕是否燒進影片；關掉只輸出字幕檔 |
| `subtitle_font` | `Noto Sans CJK TC` | 燒字幕用的字體（系統要裝得到；Docker 映像裡有 Noto CJK，Mac 請改成 `PingFang TC` 之類） |
| `normalize_audio` / `target_lufs` | `true` / `-14` | 成片的響度標準化（整合響度 LUFS，-30 到 -5）；TTS 常常偏小聲，開著比較保險 |
| `work_dir` / `out_dir` | `work` / `out` | 中間產物與成品 |
| `record_jobs` | `true` | 長指令是否在 `work_dir/_jobs` 留下紀錄（網頁看得到）；關掉就不寫 |

## split／speak：分句與逐句旁白

字幕的時間軸來自「逐句合成、量每句 WAV 的長度」，不靠 ASR——精準而且確定。所以分句的方式直接決定字幕的節奏。

```bash
vidpipe split script.txt                  # 只看怎麼分句：[{index, text, chars}]
vidpipe split --text "台北的夜晚，從一碗熱湯開始。雨剛停！"
vidpipe speak script.txt                  # 分句 → 逐句 TTS → 修頭尾靜音 → 每句的 wav 路徑與秒數
vidpipe speak script.txt --out-dir wavs   # 另外複製成 001.wav、002.wav…
```

**分句規則**
- 句末標點（`。！？；!?;…`）與換行一定切；標點後面的收尾引號、括號留在同一句（`他說：「走吧。」`）。
- 英文句點只在真的句末才切：`3.5`、`v1.2`、`example.com`、`Mr. Lin` 不會被切開。
- 句子超過 `--max-chars`（預設 40 字）就在逗號、頓號處切成幾段，每段盡量填滿但不超過上限；
  沒有逗號可切的長句維持原樣，不硬砍字。
- 只剩標點的碎片併回前一句。短句（例如「夠了。」）不會被合併。

**合成**
- **快取**：同樣的（文字、模型、音色參考、逐字稿）不重複合成，存在 `work_dir/cache/tts/<雜湊>.wav`。
  改一個字只重做那一句；換模型或音色整批重做；換服務位址不影響。
- **修靜音**：TTS 的頭尾常有空白，修掉頭尾（各留 50 毫秒），句與句之間才不會有莫名的停頓。
- **並行度**用 `tts_concurrency` 設；結果順序永遠跟輸入一致，相同的句子只合成一次。
- 任何一句失敗或被取消，還沒開始的句子不再送出；已完成的留在快取，修好重跑只補缺的。
- 是長指令，會留下工作紀錄（`narrate`），網頁看得到逐句進度。
- 真實的 audio.cpp 要先載入模型；CosyVoice3 還要 `voice_ref` 與 `reference_text`（`vidpipe doctor` 會檢查）。

## manifest：宣告式描述一支片

agent 寫一份 JSON，`validate`／`narrate`／`render` 都只讀它；同一份 manifest 重跑得到同一支片。

```json
{
  "schema": "vidpipe.manifest/1",
  "id": "taipei-night-01",
  "title": "台北夜市",
  "output": {"aspect": "9:16", "fps": 30},
  "voice": {"model": "", "voice_ref": "", "reference_text": ""},
  "timing": {"lead": 0.15, "gap": 0.25, "tail": 0.45},
  "scenes": [
    {"id": "s1", "text": "台北的夜晚，從一碗熱湯開始。",
     "clip": {"asset_id": "pixabay:123", "start": 3.0}, "fit": "blur_pad", "focus": "0.5,0.5"},
    {"id": "s2", "sentences": [{"text": "夜市很熱鬧。", "en": "The market is lively."}]},
    {"id": "s3", "min_duration": 2.0}
  ],
  "subtitles": {"langs": "zh-TW", "burn_in": true, "style": "bold-bottom"},
  "music": {"path": "/path/bgm.mp3", "start": 0, "volume": 0.3, "duck_under_voice": true}
}
```

- 只有 `schema`、`id`、`scenes` 必填。`id` 是小寫英數、底線、連字號（最長 64 字），會當資料夾名稱。
- 旁白給 `text`（自動分句）或 `sentences`（明確列出每一句；雙語字幕要配英文時用），兩個擇一。
  沒有旁白的場景要給 `min_duration`（秒，最長 120）。
- `voice` 寫了就蓋過設定檔與環境變數（寫空字串 = 明確要伺服器預設）；`subtitles` 沒寫的欄位用設定檔的 `subtitle_langs`／`burn_subtitles`。
- **不認得的欄位一律報錯**（多半是打錯字），訊息帶路徑與可用欄位，例如 `manifest scenes[1]：不認得的欄位 txt`。
- 每個場景的 `clip` 擇一給 `asset_id`（vidgrab 的素材 id）或 `path`（本機檔案）；素材怎麼取得與裁切見下面的 `render`。

```bash
vidpipe validate shorts.json   # 只檢查，不呼叫 TTS：每個場景的分句、字數、粗估秒數
vidpipe narrate shorts.json    # 逐句合成旁白 → 排時間軸 → 寫 work_dir/shorts/<id>/
```

### narrate 做什麼

每個場景照這個節奏排：

```
場景開頭 ─ lead ─ 第一句 ─ gap ─ 第二句 ─ … ─ 最後一句 ─ tail ─ 場景結束
```

- **場景長度** = max(`min_duration`, 旁白結束 + `tail`)，**向上取整到整數幀**，所以每個場景的起點都落在幀邊界上，
  逐場景渲染再串接不會累積誤差。沒有旁白的場景就是 `min_duration`。
- **字幕時間**是每一句的絕對起訖秒數，來自實際合成出來的長度。
- 總長超過 60 秒會在 `warnings` 提醒；超過 3 分鐘就不會被 YouTube 當成 Shorts。
- 輸出在 `work_dir/shorts/<id>/`（放在 `shorts/` 底下，作品 id 叫 `cache` 也不會蓋到 TTS 快取）：

| 檔案 | 內容 |
| --- | --- |
| `manifest.json` | 驗證過的原稿 |
| `timeline.json` | 每個場景的幀數與起訖、每一句的起訖與英文、用的音色、`fingerprint`（`render` 用它確認沒有過期） |
| `narration.wav` | 每句旁白擺在它的起點、其餘補靜音，長度等於整支片（24kHz 單聲道） |
| `subs.srt`、`subs.ass` | 字幕（見下一節）；整支片都沒有旁白就不產生 |

- `timeline.json` 與 `narration.wav` 對同一份 manifest 是逐位元組相同的（快取命中與否不寫進檔案），
  檔案裡的句子音檔只寫檔名（在 `work_dir/cache/tts/`），所以換機器、換掛載點也還能用。
- 整支片都沒有旁白（純音樂）也行：照 `min_duration` 排時間軸，不產生 `narration.wav` 與字幕。
- 是長指令，會留下工作紀錄（`narrate`，帶 `short_id`），網頁看得到逐句進度。

### 字幕：subs.srt 與 subs.ass

`narrate` 順便把字幕檔也寫好——時間直接取自每一句旁白，所以字幕跟聲音對得上。
改字幕的語言或樣式只要重跑 `narrate`（旁白走快取，不會再呼叫 TTS）。

- `subs.srt`：給播放器或上傳；`subs.ass`：燒進影片用（解析度與輸出一致，字級、邊距、描邊都已定好）。
- **一個畫面最多兩行中文**。太長的句子（40 字大約 3 行）會在逗號附近分成幾頁，每頁的顯示時間依字數比例分配。
- **折行**（中文沒有空白可斷，所以自己折）：優先在逗號、頓號後面斷、兩行盡量一樣長；避頭尾（`，。！？」` 不開頭、`「（` 不結尾）；
  英文與數字不從單字中間斷開。
- **雙語**（`"subtitles": {"langs": "zh-TW+en"}`）：中文在上、英文在下（較小、淡黃）。句子分頁時英文依各頁中文字數分配，
  盡量切在英文的逗號處。**沒寫 `en` 的句子只顯示中文**，`warnings` 會告訴你有幾句、在哪些場景。
  只寫 `zh-TW` 時，就算有 `en` 也不顯示。
- **停留**：聲音結束後多停 0.15 秒（不超過下一句開始），太短的句子至少顯示 0.4 秒；任兩條字幕不重疊。
- **位置**：放在底部，並留出 Shorts 介面（標題、頻道名）會蓋住的空間——直式離底邊 20%、橫式 8%（這是估計值，
  各版本的介面不完全一樣）。字級與每行字數都以輸出的短邊為準：9:16 約 65px、每行 14 個中文字。
- 字體用設定的 `subtitle_font`。**字體裝不到時 libass 會換成別的字體，中文可能變方塊**——Docker 映像已裝 Noto CJK；
  Mac 請把 `VIDPIPE_SUBTITLE_FONT` 設成裝得到的繁中字體（例如 `PingFang TC`）。

## render：照 manifest 渲染成片

```bash
vidpipe narrate shorts.json     # 先有旁白、時間軸與字幕
vidpipe render shorts.json      # → out/<id>.mp4（-o 指定輸出檔）
```

```
每個場景：取素材 → 裁切、補長、重構圖 → 無聲、剛好 N 幀的片段（有快取）
片段們 ── 串接（不重編碼）──┬─ 燒字幕（重編碼）或直接複製影像
narration.wav ──────────────┴─ 編成 AAC ──→ out/<id>.mp4
```

- **素材**（`clip`）有兩種寫法：
  - `{"path": "/a/b.mp4", "start": 3}`：本機檔，`start` 是檔案裡的第幾秒。
  - `{"asset_id": "pixabay:123", "start": 3}`：vidgrab 的素材，`start` 是**整支影片**的第幾秒。
    vidpipe 只向 vidgrab 要 `start` 到 `start + 場景長度` 這一段（多留 0.5 秒），不下載整支；
    素材庫裡已經有涵蓋這一段的片段（或整支）就直接用，不重抓（vidgrab 的批次本來就是冪等的）。
    asset id 也可以是片段 id（`pixabay:123#2-8`）：那個檔案本身就是裁好的片段，`start` 改成相對於片段檔的開頭。
  - vidgrab 跟 vidpipe 在同一台機器、檔案讀得到時直接用那個路徑；不同機器或容器就走 `/file/...` 下載到
    `work_dir/cache/assets/`。要設定 `VIDPIPE_VIDGRAB_URL`（與需要時的 `VIDPIPE_VIDGRAB_TOKEN`）。
- **場景長度由 `narrate` 的時間軸決定**（整數幀），片段是精確的幀數，串接是 `-c copy`，所以聲音與畫面不會漂移。
  素材不夠長就循環補長（只循環 `start` 之後的部分）；沒有 `clip` 的場景是黑畫面。兩者都會寫進 `warnings`。
- **重跑只做改過的**：每個場景片段的檔名帶內容雜湊（素材檔、起點、幀數、輸出規格、`fit`、`focus`），
  沒動的場景直接用快取（結果裡 `scenes[].cached`），改過的舊片段在結束時清掉。中間檔在 `work_dir/shorts/<id>/render/`。
- **不會用過期的旁白渲染**：`render` 會檢查 `timeline.json` 是不是從現在這份 manifest（加上音色、字幕設定）算出來的；
  改過就回 `bad_input`（exit 2），提示重跑 `narrate`（沒改的句子走快取，很快）。還沒跑過 `narrate` 則回 `not_found`。
- **字幕**：`subtitles.burn_in` 為 true 就燒進畫面（重編碼一次）；false 就把 `.srt` 放在 mp4 旁邊（同檔名）。
- **聲音**：旁白、背景音樂（見下一節），整支片標準化到目標響度；素材原本的聲音一律靜音
  （`mute_source: false` 目前會警告後仍靜音，環境音混音在後續 PR）。沒有旁白也沒有音樂的片會有一軌靜音，方便上傳與串接。
- **出處**：`work_dir/shorts/<id>/sources.json`（`vidpipe.sources/1`）記下每個場景用了哪個素材、作者、授權與
  `credit_hint`，之後的 credits 用它。vidgrab 的授權分類不是 `free`／`free-attribution` 的素材，`warnings` 會提醒公開前先確認。
- 是長指令，會留下工作紀錄（`render`，階段 `assets`／`segments`／`audio`／`mux`，帶 `short_id`）。

### 背景音樂與響度

manifest 的 `music` 給 `path`（本機音訊檔，有音訊軌的影片也行）或 `asset_id`（vidgrab 的音樂庫素材或「只要聲音」抓下來的
m4a；取檔規則跟影片素材一樣，只要 `start` 起算、整支片那麼長的一段）。

| 欄位 | 預設 | 意思 |
| --- | --- | --- |
| `start` | `0` | 從音樂的第幾秒開始用（跳過前奏）；音樂不夠長就循環 `start` 之後的部分 |
| `volume` | `0.3`（`duck_under_voice` 關掉時 `0.1`） | **音樂相對於旁白的響度**（1 = 一樣大聲，0.3 ≈ 小 10 dB）。`0` = 不放音樂 |
| `duck_under_voice` | `true` | 旁白出聲時音樂再壓低約 10–15 dB，停頓時回升（sidechain 壓縮，跟著旁白實際音量走） |

- `volume` 是換算過的：先分別量旁白與音樂的響度，再算增益，所以不管音樂檔的母帶音量多大，同一個 `volume` 的效果都差不多。
- 音樂淡入 0.5 秒、結尾淡出 1.5 秒（短片按比例縮短）；比整支片長就切掉，比較短就循環（接縫會有點突兀，`warnings` 會說）。
- 混好的聲音留在 `work_dir/shorts/<id>/render/mix.wav`，最後再整體量一次整合響度（LUFS），用 loudnorm 的線性模式調到 `target_lufs`
  （預設 -14）。結果的 `audio.loudness` 有「混音後量到的」`input`、`target` 與成片實測的 `output`。
- 沒有旁白、只有音樂（純音樂短片）也行：照 `min_duration` 排時間軸，音樂就是整支片的聲音（沒有東西可壓，所以不 ducking）。
- 音樂的出處與授權跟素材一樣記進 `sources.json` 的 `music`；授權不是 `free`／`free-attribution` 時 `warnings` 會提醒
  （vidgrab 的音樂庫只收 CC0、公共領域、CC BY，CC BY 要在說明欄標註作者）。

## 工作紀錄：jobs／job／cancel

`normalize`、`trim`、`fill`、`concat`、`speak`、`narrate`（之後的渲染也一樣）每次執行都會在 `work_dir/_jobs/` 留下一份狀態與事件，
**不管是從終端機、API 還是 MCP 發起的**，網頁的「工作」頁都看得到。輸出的 JSON 多一個 `job`（記錄關掉時是 `null`）。

```bash
vidpipe normalize in.mp4 -o out.mp4    # → {"ok": true, "job": "20261108-135347-da34df", "output": {…}}
vidpipe jobs --status running          # 清單（新的在前）
vidpipe job 20261108-135347-da34df     # 狀態、進度、階段、錯誤，加上事件（--after N 只要 N 之後的）
vidpipe cancel 20261108-135347-da34df  # 取消還在跑的工作
```

- 狀態：`running`、`succeeded`、`failed`、`cancelled`、`interrupted`。**`interrupted`** 是狀態檔寫著 running、但行程已經不在
  （被 `kill -9`、機器重開）時對外顯示的狀態，不會改寫檔案。
- 進度來自 ffmpeg 的 `-progress`，所以進度條是真的，不是假動畫。失敗時帶結構化錯誤（`code`／`message`／`hint`）。
- 取消 = 對該行程送 `SIGTERM`：ffmpeg 被殺掉、`.partial` 暫存檔清掉、狀態記成 `cancelled`。
  **只在同一台機器上做得到**，而且送之前會確認那個 pid 現在真的是 vidpipe（pid 會被重用，不確認可能殺到別的行程）。
- 記錄是盡力而為：`work_dir` 寫不進去時指令照跑，只是不留紀錄。不想留紀錄就 `VIDPIPE_RECORD_JOBS=false`。
- **CLI 與伺服器要看同一個 `work_dir`**：預設的 `work` 是相對路徑（相對於執行當下的目錄），
  想讓不同地方跑的指令都出現在同一個網頁，請用絕對路徑設 `VIDPIPE_WORK_DIR`。

## 網頁介面

```bash
vidpipe serve                      # http://127.0.0.1:8320，會開預設瀏覽器；--no-open 關掉
vidpipe serve --port 8080
```

目前有三頁：**總覽**（ffmpeg／vidgrab／TTS 三張狀態卡，每 30 秒重新檢查，失敗時顯示原因與 hint；
下方是最近的工作）、**工作**（每個長指令的狀態、進度條、階段、參數、結果、事件記錄、失敗原因，進行中的可以取消）、
**設定**（目前生效的設定，token 遮蔽）。作品列表、成品預覽隨後續 PR 加入，規劃見 `vidpipe-web-ui-plan.md`。

API（皆為 JSON，錯誤格式同 CLI）：

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/version` | 版本與 build（公開，給 healthcheck） |
| GET | `/api/doctor?skip=…` | 等同 `vidpipe doctor` |
| GET | `/api/config` | 生效設定（祕密遮蔽） |
| GET | `/api/jobs?status=&limit=` | 工作清單（新的在前，最多 200） |
| GET | `/api/job/{id}[/events]?after=N` | 單一工作與事件（`after` 之後的） |
| POST | `/api/job/{id}/cancel` | 取消還在跑的工作 |

**安全**：這個服務會呼叫 TTS、讀寫 work 目錄，所以預設只綁 `127.0.0.1`。

| 情況 | 行為 |
|---|---|
| 綁非本機位址，沒加 `--allow-lan` | 拒絕啟動（exit 2） |
| 綁非本機位址，沒設 `VIDPIPE_API_TOKEN` | 拒絕啟動；外層已有保護時才用 `--no-auth` |
| 設了 token | `/api/*` 要 `Authorization: Bearer`；瀏覽器開一次 `/?token=…` 換 HttpOnly、SameSite=Strict cookie。外殼、`/static/*`、`/api/version` 公開 |
| `Host` 標頭 | 只認本機名稱與 IP 字面值（擋 DNS rebinding）；用網域名稱連要放進 `VIDPIPE_ALLOWED_HOSTS`（逗號分隔） |
| 寫入類請求 | 帶了 `Origin`／`Referer` 就必須跟 `Host` 同源（擋 CSRF）；curl 這類沒帶的放行 |

Docker 裡必須綁 `0.0.0.0`，所以要 `--allow-lan` 與 token；對外範圍由 `-p` 決定，只想本機用就寫 `127.0.0.1:`：

```bash
docker run --rm -e VIDPIPE_API_TOKEN="$(openssl rand -hex 24)" -p 127.0.0.1:8320:8320 \
  -v "$PWD/work:/data/work" -v "$PWD/out:/data/out" vidpipe serve --host 0.0.0.0 --allow-lan --no-open
```

### 前端

React 19 + TypeScript + Vite，原始碼在 `web/`，**打包產物輸出到 `src/vidpipe/static/` 並進版控**，
所以 pip 與 Docker 裝完就能用，不需要 node。

```bash
cd web && npm ci        # 只有第一次
npm run build           # tsc --noEmit && vite build && 寫入 .build-stamp
npm run typecheck       # 只做型別檢查
tools/check_frontend.sh # 產物與原始碼是否同步（CI 會跑）
```

## doctor

一次檢查整條流水線需要的東西，輸出 JSON，全部必要項目通過才回 exit 0：

| 項目 | 必要 | 檢查什麼 |
|---|---|---|
| `ffmpeg`／`ffprobe` | 是 | 有沒有裝 |
| `ffmpeg_subtitles` | 燒字幕時 | ffmpeg 有沒有 `subtitles` 濾鏡（libass） |
| `cjk_font` | 否（警告） | 有沒有 CJK 字型，沒有字幕會變方塊 |
| `dirs` | 是 | `work_dir`／`out_dir` 可寫 |
| `vidgrab` | 是 | 連得到、token 對（`/api/version` 與 `/api/projects`） |
| `tts` | 是 | `/health` 正常、有已載入的模型（指定了 `tts_model` 就要在清單裡） |
| `voice_ref` | 否（警告） | 音色參考音檔存在（路徑是 TTS 那台機器上的，不同機器可忽略） |

`--skip vidgrab,tts` 略過某些項目。audio.cpp 重啟後模型要重新載入，`tts` 檢查會直接告訴你。

## normalize：統一規格與 9:16 重構圖

任何影片 → 同一種規格（H.264 yuv420p、固定 fps、`setsar=1`、AAC 48 kHz 立體聲），之後
裁切、串接才不會因為編碼不一致出問題。不管來源是 vidgrab、Pexels 或自己的檔案都一樣。

```bash
vidpipe probe clip.mp4                                    # 時長、解析度、fps、編碼、有無音軌
vidpipe normalize clip.mp4 -o out.mp4                     # 預設 9:16（1080x1920）、30fps、blur_pad
vidpipe normalize clip.mp4 -o out.mp4 --fit crop --focus 0.3,0.5
vidpipe normalize clip.mp4 -o out.mp4 --aspect 1:1 --audio silent
```

| 選項 | 值 | 說明 |
|---|---|---|
| `--aspect` | `9:16`（預設）、`1:1`、`16:9` | 輸出 1080x1920／1080x1080／1920x1080 |
| `--fit` | `blur_pad`（預設）、`crop`、`letterbox` | 長寬比對不上時：模糊放大當底＋原片置中／裁切填滿／黑邊 |
| `--focus` | `x,y`（0–1） | 只對 `crop` 有用，決定保留哪一塊；`0,0.5` = 靠左，預設置中 |
| `--fps` | 整數，預設 30 | 輸出固定 fps |
| `--audio` | `keep`（預設）、`silent` | `keep` 保留原音；`silent` 換成靜音軌（旁白會另外混進來） |

輸出**一定有音軌**（原片沒聲音就補靜音），這樣後面串接、混音不需要分情況。輸出先寫到
`.<name>.partial.mp4`，成功才改名，所以中斷不會留下半個成品。

## trim／fill／concat：裁切、補長、串接

輸入是 `normalize` 過的片段，輸出維持同一規格（`trim`／`fill` 沿用輸入的 fps，不會偷偷改）。

```bash
vidpipe trim in.mp4 -o out.mp4 --start 3 --duration 2.5   # 精準裁切；省略 --duration = 到尾
vidpipe fill in.mp4 -o out.mp4 --duration 6 --start 3     # 湊成剛好 6 秒：夠長就裁，不夠就循環
vidpipe fill in.mp4 -o out.mp4 --duration 6 --no-loop     # 不夠長就報 clip_too_short，換下一支素材
vidpipe concat a.mp4 b.mp4 c.mp4 -o all.mp4               # 依序串接
```

- **裁切一律重編碼**，不用 `-c copy`：copy 只能切在關鍵幀上，素材關鍵幀稀疏時會差好幾秒。
- **`fill` 的循環只循環 `--start` 之後的部分**（不會把起點前的內容混進來）。回傳多一個 `looped`。
- **`concat` 先驗證規格一致**：尺寸、fps、取樣率、都要有音軌；不一致時指出是哪一支、差在哪，
  並提示先 `normalize`。預設用 concat 濾鏡重編碼，接縫的音畫都準；`--copy` 走 demuxer，
  不重編碼、零畫質損失，但 AAC 在接縫處可能有幾十毫秒誤差（另外要求編碼也完全一致）。
- 素材不夠長（`clip_too_short`）與規格不一致都是 `bad_input` 類錯誤，exit code 2。

## 錯誤格式

跟 vidgrab 相同，agent 兩邊用同一份處理邏輯：

```json
{"ok": false, "error": {"code": "bad_config", "message": "…", "retryable": false, "hint": "…"}}
```

上游（vidgrab、TTS）出錯時 code 是 `upstream_error`，並多帶 `upstream`（哪個服務）與
`upstream_code`（對方自己的錯誤碼），`retryable` 沿用對方的判斷。

## 開發

```bash
ruff check . && pytest -q
cd web && npm ci && npm run build   # 只有動到前端才需要
```

## 授權

MIT。素材與模型各有自己的授權，使用前請看 vidgrab 回報的 `license` 與 `credit_hint`。
