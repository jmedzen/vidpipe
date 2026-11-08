# AGENTS.md — 給在這個 repo 工作的 agent

vidpipe 是 Shorts 流水線：腳本 → 旁白與字幕 → 素材 → 9:16 成品。
上游是 [vidgrab](https://github.com/jmedzen/vidgrab)（找素材）與 audio.cpp（本機 TTS，
`POST /v1/audio/speech`）；渲染一律用 ffmpeg。完整計畫與 PR 切分見使用者專案的
`shorts-pipeline-plan.md`（階段 0–6）。

## 原則

- **stdout 只有 JSON**（`{"ok": true, ...}` 或 `{"ok": false, "error": {code, message, retryable, hint}}`），
  人看的訊息走 stderr。錯誤格式跟 vidgrab 同一套，改動要兩邊一起想。
- **設定是資料不是程式**：TTS 模型、音色參考音檔、逐字稿、字幕語言都在 `config.py`，
  環境變數 `VIDPIPE_<NAME>` > `vidpipe.json` > 預設。模型尚未決定，不要寫死。
- **字幕**：`zh-TW` 或 `zh-TW+en`（雙語）兩種都要支援；`burn_subtitles` 預設 `true`，可關。
- **渲染是純函式**：同一份 manifest 重跑得到同一支片；中間產物以內容雜湊快取。
- **授權**：選到授權未標示的素材要在 manifest 與 credits 明確標出，不默默放行。
- **9:16 與統一編碼屬於這個 repo**，不要塞回 vidgrab（vidgrab 只負責找與取）。
- 祕密（token、API key）只進環境變數；`vidpipe config` 一律遮蔽，測試要驗證不外洩。

## 工作紀錄

任何會跑超過一兩秒的指令都要過 `jobs.JobRecorder`（CLI 用 `cli._recorded`），讓網頁與 agent 看得到進度：
- 渲染程式碼不直接依賴記錄：用 `jobs.stage("名稱", "說明")` 開階段，沒有人在記錄時它什麼都不做；
- 呼叫 `ffmpeg.run(args, duration=預期輸出秒數)`，進度就會自動回報；沒給 `duration` 就不回報；
- 記錄不能讓指令失敗：寫檔錯誤一律吞掉並停用記錄（`JobRecorder.enabled = False`）；
- 取消走 `SIGTERM → jobs.Cancelled`，`ffmpeg.run` 會先殺掉子行程再往外丟；暫存檔用 `finally` 清掉；
- 伺服器送 `SIGTERM` 前一定先確認 pid 是 vidpipe（`jobs._is_vidpipe_process`），不要放寬。

## 網頁介面

`vidpipe serve`：標準庫 `ThreadingHTTPServer`（`server.py`）＋ React 19／TypeScript／Vite（`web/`），
做法跟 vidgrab 一致。規劃見使用者專案的 `vidpipe-web-ui-plan.md`。

- **前端產物進版控**：`npm run build` 輸出到 `src/vidpipe/static/`，pip 與 Docker 直接帶著走，使用者不需要 node。
  改了 `web/`（`src/`、`index.html`、`public/`）一定要 `cd web && npm run build`；
  CI 的 `tools/check_frontend.sh` 會比對 `static/.build-stamp`，忘了 build 就紅。
- **防護不能漏**：新增寫入類端點（POST／PUT／DELETE）走 `Handler._write`，Host 白名單、token、Origin 檢查已經在入口；
  `/file/*` 之類讀檔路由一律用 `relative_to` 比目錄層級（不要用字串前綴），並補 `..`／同前綴目錄的測試。
- 外殼的 CSP 是 `script-src 'self'`：**不要在 `index.html` 寫 inline script**（要在 paint 前做的事放 `web/public/`）。
- 前端 PR 要附 Playwright 截圖（深色與淺色各一張、窄螢幕一張），自己先看過。

## 開發

```bash
pip install -e ".[dev]"
ruff check . && pytest -q
cd web && npm ci && npm run build      # 只有動到前端才需要
```

測試必須離線：ffmpeg 用合成影片，TTS 與 vidgrab 用假伺服器。
需要真實 TTS 的驗收只能在使用者的 Mac 上跑（audio.cpp 在那邊，雲端連不到）。
