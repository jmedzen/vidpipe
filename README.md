# vidpipe

Shorts 流水線：**腳本 → 旁白與字幕 → 素材 → 9:16 成品**。

- 素材：[vidgrab](https://github.com/jmedzen/vidgrab)（B 站／YouTube／Pexels／Pixabay，帶授權資訊）
- 旁白：audio.cpp 本機 TTS（任何 OpenAI 風格的 `/v1/audio/speech` 都行，例如 ttsgrab）
- 渲染：ffmpeg（9:16 重構圖、統一編碼、字幕、混音）
- 給 AI agent 用：CLI（stdout 只有 JSON）、之後有 HTTP API 與 MCP
- 給人看：網頁介面（`vidpipe serve`），看三個服務的狀態、之後還有進度與成品預覽

> 狀態：階段 1 完成，網頁介面起步。目前有 `version`、`config`、`doctor`、`probe`、`normalize`、`trim`、`fill`、`concat`、`serve`；旁白、字幕依序在後續 PR。

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
| `voice_ref` / `reference_text` | 空 | 音色參考音檔與逐字稿（CosyVoice3 等需要） |
| `subtitle_langs` | `zh-TW` | `zh-TW` 或 `zh-TW+en`（雙語） |
| `burn_subtitles` | `true` | 字幕是否燒進影片；關掉只輸出字幕檔 |
| `work_dir` / `out_dir` | `work` / `out` | 中間產物與成品 |

## 網頁介面

```bash
vidpipe serve                      # http://127.0.0.1:8320，會開預設瀏覽器；--no-open 關掉
vidpipe serve --port 8080
```

目前有兩頁：**總覽**（ffmpeg／vidgrab／TTS 三張狀態卡，每 30 秒重新檢查；失敗時顯示原因與 hint）與
**設定**（目前生效的設定，token 遮蔽）。作品列表、進度、成品預覽隨後續 PR 加入，規劃見
`vidpipe-web-ui-plan.md`。

API（皆為 JSON，錯誤格式同 CLI）：`GET /api/version`（公開，給 healthcheck）、`GET /api/doctor?skip=…`、
`GET /api/config`。

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
