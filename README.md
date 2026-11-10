# vidpipe

Shorts 流水線：**腳本 → 旁白與字幕 → 素材 → 9:16 成品**。

- 素材：[vidgrab](https://github.com/jmedzen/vidgrab)（素材庫：Pixabay／Pexels 免費素材、B 站／YouTube，音樂有 Openverse／Jamendo，都帶授權資訊；Pexels 目前不好申請 API key，主力是 Pixabay）
- 旁白：audio.cpp 本機 TTS（任何 OpenAI 風格的 `/v1/audio/speech` 都行，例如 ttsgrab）
- 渲染：ffmpeg（9:16 重構圖、統一編碼、字幕、混音）
- 給 AI agent 用：CLI（stdout 只有 JSON）、HTTP API、MCP（stdio 與 HTTP 兩種）；
  完整做法見 [docs/agent-recipe.md](docs/agent-recipe.md)
- 給人看：網頁介面（`vidpipe serve`），看三個服務的狀態、每支片做到哪、預覽成品、進度與工作紀錄

> 狀態：整條流水線都能用了：`plan`（挑素材與音樂）→ `narrate`（逐句旁白、時間軸、字幕）→ `render`（9:16 成片、背景音樂 ducking、響度標準化、封面、credits），或 `make` 一次做完；`doctor` 檢查環境。入口有四種：CLI、HTTP API、MCP（stdio 與 HTTP，含 `plan_short`）、網頁介面（`vidpipe serve`：作品頁、編輯場景、換素材、可直接改設定的設定頁、六組主題；macOS 可點兩下 `start-vidpipe-8320.command`）。

> **從主題到可上傳的影片**（選題、腳本、審稿、挑素材、包裝、品質檢查）在 [`studio/`](studio/README.md)：
> 它在 vidpipe 的上游，成片仍然是呼叫 vidpipe。不進 Docker 映像，不影響 `vidpipe serve`。

## 快速開始

```bash
pip install -e ".[dev]"
vidpipe version
vidpipe config          # 目前生效的設定（token 會遮蔽）
vidpipe doctor          # 檢查 ffmpeg、vidgrab、TTS 是否都就緒
```

macOS 可以直接點兩下 `start-vidpipe-8320.command`（仿 audio.cpp 的 `start-server-8301.command`）：
用 `.venv/bin/vidpipe`（或 PATH 上的 `vidpipe`）在 8320 起網頁並開瀏覽器，字幕字體預設 `PingFang TC`。

### Docker

完整組合（vidpipe 網頁介面＋vidgrab＋連主機上的 audio.cpp）：

```bash
git clone https://github.com/jmedzen/vidgrab ../vidgrab    # compose 從這裡建 vidgrab 映像（可用 VIDGRAB_SRC 改位置）
cp .env.example .env     # 填 VIDPIPE_API_TOKEN、VIDGRAB_API_TOKEN（openssl rand -hex 24）與 PIXABAY_API_KEY 等
./tools/compose-up.sh                 # = docker compose up -d --build，並帶入兩邊 git 的 build number
# → http://127.0.0.1:8311（帶 token；vidgrab 在 8310）；docker compose ps 看 healthy
docker compose exec vidpipe vidpipe doctor     # 檢查 ffmpeg、vidgrab、TTS
```

- **部署**：vidgrab 與 vidpipe 跑在 NAS 的 Docker（不需要 GPU）；audio.cpp 留在 MacBook Pro 本機（要 GPU），不進容器。
  NAS 的容器經區網連它：`.env` 的 `VIDPIPE_TTS_BASE_URL=http://<Mac 的區網 IP>:8301`（Mac 要有固定 IP）。
  不能用 `localhost`、`host.docker.internal`。`VIDPIPE_VOICE_REF` 是 **Mac 上**的路徑，不是 NAS 或容器內的。
- **audio.cpp 要改什麼**：它預設只聽 `127.0.0.1`，NAS 連不到。`start-server-8301.command` 要加 `--host <Mac 的 IP>`
  （綁在區網 IP 比 `0.0.0.0` 好，不會多開到其他介面）。**注意**：這會讓區網內的人連得到它，包含 `--ui-management` 管理介面，
  我不確定它有沒有密碼。不需要管理介面就拿掉 `--ui-management`；再用 macOS 防火牆只放行 NAS 的 IP 連 8301。
- **架構**：映像用 Debian 的 python:3.12-slim，amd64 與 arm64 都有；NAS 是 ARM 就直接在 NAS 上 `./tools/compose-up.sh` 建，不用交叉編譯。
- **字幕字體**：映像有 ffmpeg 與 Noto Sans CJK TC（`VIDPIPE_SUBTITLE_FONT` 預設值），中文字幕不會變方塊。
- **資料**：`/data/work`（中間產物、旁白快取）、`/data/out`（成品）、`/data/config`（網頁「設定」頁存的 `vidpipe.json`）各是一個 volume，重建容器後都還在；vidgrab 的素材庫在它自己的 volume。NAS 上要放共用資料夾，在 `.env` 設 `VIDGRAB_DATA_DIR`／`VIDPIPE_WORK_DIR`／`VIDPIPE_OUT_DIR`（資料夾要讓 uid 1000 寫得進去）。
- **設定頁的鎖**：`.env` 裡寫了值的項目（token、TTS 位址、vidgrab 位址等）在設定頁會顯示「由環境變數指定」並鎖住，只能改 `.env` 再重啟；留空的項目（字幕語言、字體等）才能在網頁改並存進 `/data/config`。
- **build number**：網頁左上角與 `/api/version` 顯示 `commit數.短sha`，每次 commit 都會變，可確認瀏覽器載到的是新版（頁面與靜態檔本來就是 `no-cache`）。Docker 建置沒有 `.git`，所以要用 `tools/compose-up.sh`（或單獨建用 `tools/docker-build.sh`）帶入；直接 `docker compose up --build` 會顯示 `dev`。
- **安全**：容器以非 root（uid 1000）執行；port 預設只對 `127.0.0.1` 發佈，`VIDPIPE_API_TOKEN` 必填。

只跑 vidpipe 一個映像（vidgrab 在別處）：

```bash
./tools/docker-build.sh                      # 自動帶 build number
docker run --rm vidpipe version
```

完整組合用本 repo 的 [`docker-compose.yml`](docker-compose.yml)、環境變數範例在 [`.env.example`](.env.example)。
容器之間用服務名連（`VIDPIPE_VIDGRAB_URL=http://vidgrab:8310`，compose 已設好），
但瀏覽器看不到那個名字，所以要另外設 `VIDPIPE_VIDGRAB_PUBLIC_URL`（NAS 的 IP 加 8310），
總覽頁「素材來源 · vidgrab」的「開啟 vidgrab」按鈕才會指到對的地方。

### Synology DSM（Container Manager）：貼上就能跑

NAS 上不想 clone、不想 build，用 [`docker-compose.dsm.yml`](docker-compose.dsm.yml)：整份貼進
Container Manager →「專案」→ 新增 →「建立 docker-compose.yml」。裡面沒有 `${變數}`，不需要 `.env`，
只要改標了「★ 要改」的幾行（`VIDGRAB_API_TOKEN`／`VIDPIPE_VIDGRAB_TOKEN` 要同一串、`VIDPIPE_API_TOKEN`、
素材站金鑰、`VIDPIPE_TTS_BASE_URL` = Mac 上 audio.cpp 的 `http://<Mac 區網 IP>:8301`）。

- **映像**來自 GitHub Container Registry：`ghcr.io/jmedzen/vidpipe`、`ghcr.io/jmedzen/vidgrab`（amd64＋arm64），
  由各自 repo 的 `publish` workflow 在 push 到 `main` 時發佈（也可在 Actions 頁手動執行）。**第一次要先跑過一次 publish**，
  NAS 才拉得到。
- **套件預設是私有的**：pull 出現 `denied`／`unauthorized` 時，二選一——到 GitHub 的 Packages 頁把兩個套件設成 Public；
  或在 NAS 的 SSH 先 `sudo docker login ghcr.io -u jmedzen`（密碼貼有 `read:packages` 的 personal access token）。
- **網址**：vidpipe `http://<NAS-IP>:8320`、vidgrab `http://<NAS-IP>:8310`，都要輸入 token。
  「開啟 vidgrab」按鈕要到「設定」頁填 `http://<NAS-IP>:8310`（compose 裡留空，才不會被環境變數鎖住）。
- **資料**用專案資料夾下的相對路徑 bind mount（`./data/vidgrab`、`./data/work`、`./data/out`、`./data/config`），沒有用 Docker volume。
  **先建資料夾並給 uid 1000 寫入權限**（SSH：`cd <專案資料夾> && mkdir -p data/vidgrab data/work data/out data/config && sudo chown -R 1000:1000 data`），
  沒先建的話 Docker 會建成 root 的資料夾，容器一啟動就 Permission denied。成品在 `data/out`，可直接從網路磁碟拿。
- **更新**：新版映像發佈後，在 NAS 的 SSH 執行 `sudo docker compose -p <專案名> pull && sudo docker compose -p <專案名> up -d`
  （專案名就是 Container Manager 裡的專案名稱）。網頁左上角的 build 會變。

## 設定

優先序：環境變數 `VIDPIPE_<名稱>` > `vidpipe.json`（或 `VIDPIPE_CONFIG` 指定的檔）> 預設。

| 項目 | 預設 | 說明 |
|---|---|---|
| `vidgrab_url` / `vidgrab_token` | `http://127.0.0.1:8310` / 空 | 素材來源 |
| `vidgrab_public_url` | 空 | **瀏覽器**開 vidgrab 網頁用的位址（總覽頁的「開啟 vidgrab」按鈕）。空 = 從 `vidgrab_url` 推：本機位址原樣用、`host.docker.internal` 改成 `localhost`；compose 服務名（`http://vidgrab:8310`）瀏覽器打不開，推不出來，按鈕會停用並提示你設這項 |
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

- **空字串的環境變數視為沒設**：docker compose 的 `${VAR:-}` 沒值時會傳空字串，不該因此蓋掉設定檔。
- **網頁的「設定」頁可以直接改這些項目**（寫回上面那個設定檔，見[網頁介面](#網頁介面)）：被環境變數指定的項目會上鎖，
  token 存進設定檔是明文（檔案權限 600，`vidpipe.json` 不進版控），有環境變數可用時祕密放環境變數更安全。
  `VIDPIPE_SETTINGS_READONLY=1` 讓設定頁（與 API）變成唯讀，給「對外開放但不想讓人改設定」的部署。
- 上表每一項都能用 `VIDPIPE_` 加大寫名稱設定（例如 `VIDPIPE_SUBTITLE_FONT`、`VIDPIPE_TARGET_LUFS`）。
- 伺服器另外認這幾個（不在設定檔裡）：`VIDPIPE_HOST`、`VIDPIPE_PORT`、`VIDPIPE_API_TOKEN`、`VIDPIPE_NO_AUTH`、`VIDPIPE_ALLOWED_HOSTS`（見[安全](#mcp讓-claude-code-直接用-vidpipe)）。

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
  "music": {"path": "/path/bgm.mp3", "start": 0, "volume": 0.3, "duck_under_voice": true},
  "cover": {"at": 1.0, "text": "台北夜市"}
}
```

- 只有 `schema`、`id`、`scenes` 必填。`id` 是小寫英數、底線、連字號（最長 64 字），會當資料夾名稱。
- 旁白給 `text`（自動分句）或 `sentences`（明確列出每一句；雙語字幕要配英文時用），兩個擇一。
  沒有旁白的場景要給 `min_duration`（秒，最長 120）。
- `voice` 寫了就蓋過設定檔與環境變數（寫空字串 = 明確要伺服器預設）；`subtitles` 沒寫的欄位用設定檔的 `subtitle_langs`／`burn_subtitles`。
- **常駐疊字**（`overlays`，選填，最多 4 個）：整支片都在畫面上的文字，例如上方的標題問題、下方的系列名稱。
  每個是 `{"text": …, "position": "top"｜"bottom", "size": "xlarge"｜"large"｜"medium"｜"small", "color": "white"｜"yellow"}`
  （只有 `text` 必填，最長 40 字，太長自動折成兩行）。疊字跟字幕一起燒進畫面，所以要有旁白而且
  `subtitles.burn_in` 為 true，否則不畫並在 `warnings` 提醒；不會進 `.srt`，封面也不帶疊字。
  直式放在離上緣 9%／離下緣 11.5%（字幕下方、避開 Shorts 介面），橫式是 5%／2%。
- **字幕字級**：`subtitles.size` 是 `normal`（預設，短邊的 6%，9:16 約 65px）、`large`（1.25 倍）或 `xlarge`（1.5 倍）。
  字越大每行放得下的字越少，折行與分頁會跟著算。疊字的大小不跟著它變（`xlarge` 疊字是 1.6 倍，最多折成三行）。
- **不認得的欄位一律報錯**（多半是打錯字），訊息帶路徑與可用欄位，例如 `manifest scenes[1]：不認得的欄位 txt`。
- 每個場景的 `clip` 擇一給 `asset_id`（vidgrab 的素材 id）或 `path`（本機檔案）；素材怎麼取得與裁切見下面的 `render`。
- **本機檔案可以宣告授權**：`clip` 與 `music` 用 `path` 時可加 `"license"`（`own`＝自己做的、`free`＝可自由使用、
  `free-attribution`＝可用需標註）與 `"credit"`（作者與出處；`free-attribution` 必填）。宣告過的檔案在 `credits.md`
  不再標 ⚠️，`credit` 會進說明欄範本；沒宣告的照舊列進 `unclear`。`asset_id` 的授權由 vidgrab 回報，不能自己宣告。

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

## plan：交給 vidpipe 挑素材

腳本寫好、還沒挑素材時用。**草稿就是 manifest**，只是場景可以用 `query`（搜尋關鍵字）代替 `clip`，
`music` 也可以給 `query`；`plan` 去 vidgrab 搜、挑好，回一份可以直接 `make` 的 manifest。
只搜尋，不呼叫 TTS、不下載（下載發生在 `render`）。

```bash
cat > draft.json <<'EOF'
{"id": "taipei-night-01", "title": "台北夜市",
 "scenes": [
   {"text": "台北的夜晚，從一碗熱湯開始。", "query": "night market soup"},
   {"text": "夜市很熱鬧。", "query": ["street food", "crowd night"]},
   {"id": "end", "min_duration": 2.0, "query": "city lights"}],
 "music": {"query": "calm piano", "volume": 0.3},
 "search": {"platform": "stock", "orientation": "portrait"}}
EOF
vidpipe plan draft.json -o shorts.json        # → shorts.json 可以 validate、make
vidpipe plan script.txt --id taipei-night-01  # 純文字腳本：空白行隔開場景，「關鍵字：a, b」那行是 query
```

- **`query` 用英文**：Pexels／Pixabay 的索引對中文很薄。沒給 `query` 的場景會拿旁白文字去搜，並在
  `warnings` 提醒。給了 `clip` 的場景不動（`clip` 與 `query` 同時給會報錯）。
- **選片規則**（每個場景獨立挑，依序比較）：
  1. **授權**：只考慮 `free`（可自由使用）與 `free-attribution`（可用需標註），前者優先；
     `check`／`unknown` 預設不選，`allow_unknown_license` 才放行（而且會留警告）。
  2. **長度夠不夠**：素材比場景預估長度（旁白字數粗估，或 `min_duration`）加 0.5 秒還短的往後排——選了會循環補長。
     注意授權比長度重要：授權較好但太短的，仍勝過授權較差但夠長的（會警告它會循環）。
  3. **方向**：想要的方向（預設直式）優先，其次方形、尺寸不明，橫式最後。
  4. **解析度**：短邊低於 `min_height`（720）的往後排。
  5. 都一樣就照 vidgrab 的相關性順序。

  同一支素材不會用在兩個場景，除非候選只剩它。音樂的規則類似（授權 → 夠長 → 都不夠長就選最長的）。
- **`search`**（選填）：`platform`（`stock`＝Pexels＋Pixabay、`pexels`、`pixabay`、`bilibili`、
  `youtube`、`both`）、`orientation`、`per_keyword`（8）、`min_height`（720）、`allow_unknown_license`、
  `music_platform`（`music`＝Openverse＋Jamendo）、`timeout`（每次搜尋最多等幾秒）。CLI 的
  `--platform`、`--orientation`、`--allow-unknown-license` 會蓋過草稿裡的設定。
- **輸出**：`manifest`（完整、合法）與 `report`——每個場景挑了誰、為什麼（授權、方向、長度）、前 3 個備選、
  警告、`skipped_license`（因授權不明沒列入的候選數，只是資訊，不是警告）；`searches`（搜了什麼、
  vidgrab 專案名）；`without_clip`（沒挑到素材的場景）。**不滿意就改
  manifest 的 `asset_id`**（備選的 `asset_id` 直接貼上去），或調 `query` 再 `plan` 一次。
- 結果同時存成 `work/shorts/<id>/plan.json`；`plan` 是一個工作（階段 `search`、`music`），網頁看得到，
  HTTP 與 MCP 也一樣（`POST /api/plan`、`plan_short`）。同一支片同時只能有一個工作。

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
- 成片旁邊一併產出封面與素材來源清單（見下一節）。
- 是長指令，會留下工作紀錄（`render`，階段 `assets`／`segments`／`audio`／`mux`，帶 `short_id`）。

### make：一次做完

```bash
vidpipe make shorts.json     # = narrate + render，一個工作（階段 narrate、timeline、assets、segments、audio、mux）
```

結果是 `render` 的結果多一個 `narrate`（總長、TTS 合成了幾句、警告），`warnings` 是兩邊合併。agent 通常只需要這一個指令；
要微調再分開跑（例如只改封面標題用 `cover`，只改音樂音量用 `narrate` ＋ `render`，旁白走快取）。

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

### 封面與素材來源清單

`render` 在 `<成片名>.mp4` 旁邊多產出兩個檔案（`-o` 換了名字它們跟著換）：

| 檔案 | 內容 |
| --- | --- |
| `<名>.cover.jpg` | 封面：成片裡的一格，疊上標題；尺寸同成片（9:16 = 1080×1920） |
| `<名>.credits.md` | 素材來源與授權：每個影片素材與背景音樂的作者、授權、連結，以及可以直接貼進說明欄的文字 |

- **封面**取自還沒燒字幕的場景片段，所以標題不會跟字幕疊在一起。manifest 的 `cover.at` 指定取第幾秒（預設 1 秒，
  短片取中間）；標題預設用 `title`，`cover.text` 可以另外寫，寫 `""` 就不放字。標題用跟字幕同一套字體與折行
  （`subtitle_font`），放在上緣 18% 的位置，最多三行。封面做不成（例如 `at` 超過影片長度）只會在 `warnings` 提醒，不會丟掉成片。
- **credits.md**：同一個素材用在好幾個場景只列一次。授權 `free-attribution`（例如 CC BY）標「**必須標註**」；vidgrab 無法確定授權
  （`check`／`unknown`）或本機檔案標 ⚠️，公開前先確認。輸出的 `credits` 欄位摘要：`attribution_required`、`unclear`。
- 想只改封面或重做清單，不必重新渲染：

```bash
vidpipe cover shorts.json --at 3 --text "新標題"   # 用最近一次渲染的畫面重做封面
vidpipe credits shorts.json                         # 重做 credits.md
```

  這兩個指令會確認最近一次渲染就是現在這份 manifest；不是的話回 `bad_input`，提示先 `narrate`／`render`。

## 工作紀錄：jobs／job／cancel

`normalize`、`trim`、`fill`、`concat`、`speak`、`narrate`、`render`、`make`、`plan`、`cover`、`credits` 每次執行都會在 `work_dir/_jobs/` 留下一份狀態與事件，
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

目前有四頁（另有「新增作品」）：**總覽**（ffmpeg／vidgrab／TTS 三張狀態卡，每 30 秒重新檢查，失敗時顯示原因與 hint；
下方是最近的工作）、**作品**（每支片做到哪、長度、有幾個素材的授權要確認；點進去有成品播放器、
每個場景與每句旁白的時間——點句子就跳到那一秒、素材與授權（含可以直接複製的說明欄範本）、檔案、manifest、
這支片的工作；最近一次失敗會直接講原因）、**工作**（每個長指令的狀態、進度條、階段、參數、結果、
事件記錄、失敗原因，進行中的可以取消）、**設定**（見下一段）。
**不用開終端機也能做一支片**：「作品」頁右上角「＋ 新增作品」→ 貼腳本（空白行隔開場景，`關鍵字：a, b`
那行是搜素材用的）、取個 id、選填標題與音樂關鍵字 → 按「挑素材」，vidpipe 去 vidgrab 替每個場景挑素材
（`POST /api/plan`）。「挑素材」分頁列出每個場景選了什麼、為什麼、有哪些備選與提醒；沒問題就按「做成片」
（`POST /api/make`，旁白、素材、已渲染的場景都走快取，所以失敗後按「重跑」只會補做缺的）。
**六組主題**（Midnight、Ink、Forest 三深；Paper、Mist、Sakura 三淺，跟 vidgrab 同一套）在側欄的主題選單切換，沒選過就跟著系統深淺色，選擇存在瀏覽器。
「刪除」會清掉這支片的中間檔與成品（旁白快取是共用的，不動）；有工作在跑時不能刪。
**改一支做好的片**：「場景」分頁的「編輯場景」可以改標題、每個場景的旁白（`text` 或逐句的中英文）、畫面適配（`fit`）、
素材從第幾秒開始、最短秒數、背景音樂的音量與壓低設定，也能把場景或音樂換素材：「換素材」裡可以直接向 vidgrab 搜尋
（縮圖、授權徽章、直式與夠長的排前面、標出已經用在別的場景的；只搜尋、不下載任何影片），也可以選 plan 留下的備選，
或直接輸入素材 id。縮圖是 vidgrab 給的 https 圖，瀏覽器直接去載（`Referrer-Policy: no-referrer`，所以來源站看不到這個網頁的網址）。
按「儲存」才寫回 manifest（`PUT /api/short/{id}/manifest`）；這時 manifest 比成片新，頁面會提示並把按鈕變成
「重新做（套用修改）」——旁白與場景有快取，只有改到的部分會重做。編輯期間別的視窗、agent 或重新挑素材改了
manifest，儲存會被擋下（`stale_manifest`），不會蓋掉對方。

**設定頁是個完整的編輯介面**：項目依用途分組（素材來源、旁白、字幕、聲音、資料夾），每項依型別用對應的輸入——網址、
文字、路徑、多行文字、開關、帶範圍與單位的數字（輸入框加步進鈕與滑桿）、選項、token（遮蔽；只能「更換」或「清除」，
畫面上永遠看不到原本的值）。每項標著值從哪來（**環境變數**＝上鎖、改不了；**設定檔**；**預設**），並附說明；
存在設定檔的可以「改回預設」。輸入時即時檢查（跟伺服器同一套規則），不合格的項目標紅、不能儲存；沒存的修改有
「未儲存」標記，離開頁面、關分頁、重新整理前都會先問（Ctrl／⌘ + S 儲存）。儲存是原子寫入、帶 `revision`：
別的視窗、指令列或人在你編輯時改過設定檔，會被擋下並讓你「載入最新版、保留我的修改」，不會悄悄蓋掉。
設定檔壞了（不是合法 JSON、有不認得的項目）時頁面仍然打得開，可以「用預設重建」（舊檔先備份成 `.bak`）。
每一組有**測試連線**，用表單裡還沒儲存的值去測、不改任何設定：vidgrab（連得到嗎、token 對嗎）、TTS（連線、已載入的模型——
點一下填進「TTS 模型」；「試合成一句」真的請 TTS 合成一句、不留檔，驗證模型與音色參考音檔配不配）、
ffmpeg 與字體（libass 在不在；**用 libass 真的畫一格字幕、讀它的選字紀錄**，字體裝不到會直接說「改用了 X」，
不會等到成片裡出現方塊字才發現）、資料夾（可不可寫、剩多少空間，不會建立任何東西）。
儲存之後下一個請求就用新設定，進行中的工作不受影響。

想先看看畫面又不想動真資料：`python tools/make_fixture.py /tmp/vp-fixture` 會用假 TTS 與合成素材做出四支片
（做完的、有一個授權要確認的、只有旁白的、渲染失敗的），照它印出的指令 `vidpipe serve` 就能看。
加 `--serve` 會讓假服務繼續開著並直接開網頁（預設 http://127.0.0.1:8320），「新增作品 → 挑素材 → 做成片
→ 編輯 → 重新做 → 刪除」整條路都能點；`--tts-delay 2` 讓每句旁白多等 2 秒，才看得到進行中的進度條與取消。
它把假服務的位址寫進 `/tmp/vp-fixture/vidpipe.json`（不用環境變數），所以「設定」頁在這裡改得動、存得下，
「測試連線」也是綠的。

API（皆為 JSON，錯誤格式同 CLI）：

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/version` | 版本與 build（公開，給 healthcheck） |
| GET | `/api/doctor?skip=…` | 等同 `vidpipe doctor` |
| GET | `/api/config` | 生效設定（祕密遮蔽） |
| GET | `/api/settings` | 設定頁用：每個項目的分組、型別、說明、範圍、預設、現值與**來源**（`env`／`file`／`default`，`locked`＝被環境變數指定）、設定檔路徑／`revision`／可不可寫，以及設定檔的問題。**祕密沒有值**，只有 `is_set`。設定檔壞掉時照樣回 200（`/api/config` 會回 `bad_config`） |
| PUT | `/api/settings` | 存設定：`{"changes": {"tts_model": "x", "target_lufs": -16, "tts_token": "…"}, "revision": "…"}`；值 `null`＝改回預設（token＝清掉），沒列的項目不動。不合格回 400 `bad_input`，`error.fields` 是 `{項目: 原因}`，**整批都不會存**；被環境變數指定的項目也是 400；`revision` 不是現在的設定檔回 409 `stale_config`（不給就不檢查）；設定檔壞了回 409，`"replace_invalid": true` 才會覆寫（先備份成 `.bak`）；唯讀部署回 403。回傳同 GET，加 `changed` |
| POST | `/api/settings/test` | 測試連線：`{"target": "vidgrab"｜"tts"｜"ffmpeg"｜"dirs", "values": {…還沒儲存的草稿…}, "deep": true}`（`deep` 只對 `tts`：真的合成一句）。回 `checks`（跟 doctor 同格式）、`models`（TTS 已載入的）、`elapsed_ms`；不改任何設定。草稿裡 token 沒給＝沿用現有的，空字串＝當作沒有 |
| GET | `/api/jobs?status=&limit=` | 工作清單（新的在前，最多 200） |
| GET | `/api/job/{id}[/events]?after=N` | 單一工作與事件（`after` 之後的） |
| POST | `/api/job/{id}/cancel` | 取消還在跑的工作 |
| POST | `/api/validate` | 檢查 manifest（本體 `{"manifest": {…}}`）；等同 `vidpipe validate` |
| POST | `/api/make`／`narrate`／`render`／`cover`／`credits`／`plan` | 送出工作（本體 `{"manifest": {…}}`，`cover` 另可帶 `at`、`text`，`plan` 的 `manifest` 是[草稿](#plan交給-vidpipe-挑素材)，也可以改給純文字腳本 `{"script": "…", "id": "…", "title"?, "music"?, "allow_unknown_license"?}`）；回 **202** 與 `job` |
| GET | `/api/shorts` | 所有作品：階段（`new`／`planned`／`narrated`／`rendered`）、長度、素材與授權的數量（`credits`）、檔案、最近一個工作 |
| GET | `/api/short/{id}` | 單一作品：manifest（與它的來源 `manifest_source`：`manifest`／`plan`／`draft`）、`manifest_revision`、`outdated`（manifest 比上次做出來的新：`true` 要重做、`false` 是最新、`null` 還沒做過）、plan 的挑選報告（`plan`）、時間軸摘要、素材出處、檔案、最近的工作 |
| PUT | `/api/short/{id}/manifest` | 改 manifest：body `{"manifest": {...}, "revision": "<manifest_revision>"}`。驗證過才寫入；`revision` 已經不是現在那份（別的視窗、agent 或重新 plan 改過）回 409 `stale_manifest`；作品 id 改不了；有工作在跑回 409。改完 `POST /api/make` 只會重做改到的旁白與場景 |
| POST | `/api/short/{id}/search` | 替這支片的一個場景或音樂搜替換素材（不寫任何檔、不下載影片）：body `{"query": "night market, steam"（字串或陣列）, "target": "scene:<場景 id>" 或 "music", "platform"（選填，預設 `stock`／音樂 `music`）, "orientation"（選填，預設 `any`）, "allow_unknown_license"（選填）, "limit"（選填，預設 24）}`。回 `candidates`：縮圖、授權、方向、長度、`why`、`enough`（夠不夠長）、`used_in`（已經用在哪些場景）、`current`；授權不明的預設不列，只回 `skipped_license` 有幾支 |
| DELETE | `/api/short/{id}` | 刪掉這支片的中間檔與成品（旁白快取是共用的，不動）；有工作在跑回 409 |
| GET | `/file/{id}/{out\|work}/{檔名}` | 成品（`out`：mp4、封面、credits、srt）與中間檔（`work`）；支援 `Range` |

### 用 HTTP 讓 agent 做一支片（agent 與 vidpipe 不在同一台機器時）

```bash
H="Authorization: Bearer $VIDPIPE_API_TOKEN"        # 沒設 token 就不用
curl -s -H "$H" -X POST localhost:8320/api/validate -d @shorts.json   # 本體是 {"manifest": …}
JOB=$(curl -s -H "$H" -X POST localhost:8320/api/make -d "{\"manifest\": $(cat shorts.json)}" | jq -r .job)
curl -s -H "$H" localhost:8320/api/job/$JOB | jq '{status: .job.status, stage: .job.stage, progress: .job.progress}'
curl -s -H "$H" localhost:8320/api/short/taipei-night-01 | jq '.short.files[] | {name, url}'
curl -s -H "$H" -o night.mp4 localhost:8320/file/taipei-night-01/out/taipei-night-01.mp4
```

- **送件**：伺服器先驗證 manifest（不合格式立刻回 400，帶欄位路徑，**不會**開工作也不會留資料夾），存成
  `work/shorts/<id>/manifest.json`，再開一個 `python -m vidpipe <指令>` 子行程去跑。工作紀錄、進度、取消
  跟 CLI 完全是同一套（`/api/jobs`、`/api/job/{id}`、取消送 SIGTERM），伺服器重開也不會弄丟跑到一半的工作。
- **一支片同時只能有一個工作**：已經有就回 **409** `conflict`，訊息帶著正在跑的工作 id；不同的片可以同時跑。
- `make` = `narrate` ＋ `render`（含封面與 credits），一個工作做完（階段 `narrate`、`timeline`、`assets`、`segments`、`audio`、`mux`）。
  想分步做就依序送 `narrate`、`render`；`cover`／`credits` 用最近一次渲染的結果重做。
- 取檔只認清單裡的檔名（`GET /api/short/{id}` 的 `files` 會給 `url`）；用 `-o` 另外指定輸出位置的檔案不在清單裡。
- **注意**：manifest 的 `clip.path`、`music.path` 是**伺服器上**的路徑，拿得到 token 的人就能讓伺服器讀它能讀的檔案。
  只在信任的環境開放（預設綁 127.0.0.1；對外一定要設 token），或只用 `asset_id`。

### MCP：讓 Claude Code 直接用 vidpipe

跟 vidgrab 一樣，不依賴 `mcp` 套件，兩種傳輸共用同一份工具：

```bash
# stdio：Claude Code 以子行程啟動（vidpipe 與 Claude Code 在同一台機器）
claude mcp add vidpipe -- vidpipe mcp
# HTTP：連到跑著的 `vidpipe serve`（Docker、另一台機器）；有設 token 就帶上
claude mcp add --transport http vidpipe http://localhost:8320/mcp \
  --header "Authorization: Bearer $VIDPIPE_API_TOKEN"
```

| 工具 | 用途 |
|---|---|
| `manifest_guide` | manifest 的寫法、欄位與範例（第一次做片先看） |
| `validate_manifest` | 檢查 manifest（不呼叫 TTS、不碰素材）：分句、字數、粗估長度；寫錯回欄位路徑 |
| `plan_short` | 草稿（場景給 `query`）→ 搜尋並挑素材與音樂 → 可以直接 `make_short` 的 manifest 與選片報告。也收純文字腳本：給 `script` 與 `id`（選填 `title`、`music`、`allow_unknown_license`、`platform`、`orientation`），不給 `manifest` |
| `make_short` | 一次做完：旁白 → 時間軸 → 渲染 → 封面 → credits。回 job 與成品 `url` |
| `run_step` | 只做一步：`narrate`／`render`／`cover`（可帶 `at`、`text`）／`credits` |
| `get_job`／`cancel_job` | 查工作（可帶 `wait` 等它做完）／取消 |
| `list_shorts`／`get_short` | 所有作品／單一作品的狀態、素材出處、檔案網址 |
| `check_setup` | `doctor`：ffmpeg、vidgrab、TTS 就緒了沒；沒就緒**不算錯誤**，看 `ok` 與 `checks` |

- 工具只回 JSON 與網址，**不回影片內容**：成品用 `get_short` 的 `files[].url`（接在 vidpipe 的位址後面，
  支援 `Range`），同一台機器時也可以直接用工作結果裡的 `path`。
- **長工作的節奏**：`make_short`／`run_step` 預設最多等 `wait` 秒（60，上限 600）；沒做完就回
  `done: false` 與 job id，之後用 `get_job`（帶 `wait` 就會等）回頭查。stdio 每個呼叫各跑一條執行緒，
  所以 `make_short` 還在等的時候 `cancel_job`、`get_job` 照樣回得來。
- **失敗的呼叫回 `isError: true`**，內容是跟 CLI 同一套的結構化錯誤（`code`、`message`、`retryable`、`hint`）；
  失敗或被取消的工作也算（`error` 是工作失敗的原因）。
- HTTP 傳輸只有 `POST /mcp`（一則或一批 JSON-RPC；通知回 202）；`GET /mcp` 回 405（沒有 server 主動推送）。
  認證、`Host` 白名單與 Origin 檢查跟 `/api/*` 相同。
- stdio 時 stdout 只有協定訊息，其餘（log、追蹤）一律走 stderr。

**安全**：這個服務會呼叫 TTS、讀寫 work 目錄，所以預設只綁 `127.0.0.1`。

| 情況 | 行為 |
|---|---|
| 綁非本機位址，沒加 `--allow-lan` | 拒絕啟動（exit 2） |
| 綁非本機位址，沒設 `VIDPIPE_API_TOKEN` | 拒絕啟動；外層已有保護時才用 `--no-auth` |
| 設了 token | `/api/*`、`/file/*`、`/mcp` 要 `Authorization: Bearer`；瀏覽器開一次 `/?token=…` 換 HttpOnly、SameSite=Strict cookie。外殼、`/static/*`、`/api/version` 公開 |
| `Host` 標頭 | 只認本機名稱與 IP 字面值（擋 DNS rebinding）；用網域名稱連要放進 `VIDPIPE_ALLOWED_HOSTS`（逗號分隔） |
| 寫入類請求 | 帶了 `Origin`／`Referer` 就必須跟 `Host` 同源（擋 CSRF）；curl 這類沒帶的放行 |
| 改設定 | 能改設定的人等於能改 TTS／vidgrab 位址與 work／out 目錄，權限跟 token 一樣大。對外開放又不想讓人改，設 `VIDPIPE_SETTINGS_READONLY=1`（設定頁與 `PUT /api/settings` 唯讀，測試連線也只測現有的設定） |

Docker 裡必須綁 `0.0.0.0`，所以要 `--allow-lan` 與 token；對外範圍由 `-p` 決定，只想本機用就寫 `127.0.0.1:`：

```bash
docker run --rm -e VIDPIPE_API_TOKEN="$(openssl rand -hex 24)" -p 127.0.0.1:8320:8320 \
  -v "$PWD/work:/data/work" -v "$PWD/out:/data/out" -v "$PWD/config:/data/config" \
  vidpipe serve --host 0.0.0.0 --allow-lan --no-open
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
