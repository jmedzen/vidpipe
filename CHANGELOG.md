# Changelog

記錄「為什麼需要這個改動」，以及 merge 與上線驗證的結果。

## 本機檔案可以宣告授權（`clip.license`／`music.license`）

- **為什麼**：下游的 vidoutput 流水線在沒有素材站 API key 時用自己生成的背景畫面（`clip.path`）。
  這些檔案在 `credits.md` 一律被標成「本機檔案，授權請自行確認」並列進 `unclear`，
  自動品質檢查無法分辨「自製素材」與「真的沒確認過授權的檔案」。
- **改了什麼**：manifest 的 `clip` 與 `music` 用 `path` 時可加 `license`（`own`／`free`／`free-attribution`）
  與 `credit`。宣告寫進 `sources.json`（`declared_license`／`declared_credit`），`credits.md` 與摘要照宣告分類。
- **相容性**：新增的選填欄位；沒寫的 manifest 行為完全不變（schema 仍是 `vidpipe.manifest/1`）。
- **上線**：PR #27，2026-11-10 03:21Z merge；publish 成功；NAS `/api/version` = `62.8b02fa6`，`doctor` 與 `/api/validate` 通過。

## 常駐疊字（`overlays`）

- **為什麼**：短影音的常見版面是「上方一句問題整支片都在、下方是系列名稱」，觀眾任何時候滑進來都知道這支片在講什麼。
  原本只有字幕一種文字，下游流水線只能自己再疊一次字、重編碼一次。
- **改了什麼**：manifest 新增選填的 `overlays`（文字、上或下、三種大小、白或黃）。疊字寫進 `subs.ass`
  的獨立樣式，跟字幕同一次燒進畫面，不多一次編碼；不進 `.srt`。
- **相容性**：沒寫 `overlays` 的 manifest，產出的 `subs.ass` 與成片跟以前逐位元組相同。

## 旁白峰值高時，成片響度到不了目標

- **為什麼**：下游流水線的品質檢查發現成片是 -15.9 LUFS，目標 -14。TTS 旁白平均小聲（約 -21.7 LUFS）但峰值很高
  （-1.3 dBTP），加到目標響度後峰值會超過上限；loudnorm 的線性模式遇到這種情況會自己少加增益。
- **改了什麼**：加增益後峰值會超過上限時，改用「加增益＋限幅器」，並量一次結果把限幅吃掉的響度補回去
  （最多三輪，離目標 0.3 LU 以內就停）。峰值夠低的聲音照舊走 loudnorm 線性模式，結果不變。
- **驗證**：同一段真實旁白，原本 -15.9 LUFS，現在 -14.4 LUFS、真峰值 -1.4 dBTP。

## `studio/`：從主題到可上傳的影片

- **為什麼**：選題、腳本、審稿、包裝、品質檢查原本放在另一個 repo（vidoutput），跟 vidpipe「最後合成」的定位重疊。
  vidpipe 是上層，vidgrab 與 audio.cpp 是它的下游，這些上游步驟放在同一個 repo 比較好維護。
- **改了什麼**：新增 `studio/`（程式、提示詞、主題庫、文件、測試）。`studio/work`、`studio/out` 不進版控；
  `.dockerignore` 排除 `studio`，映像與線上服務不變。`pytest` 多跑 `studio/tests`（離線，假的 vidpipe 與素材池）。
- **相容性**：`src/vidpipe` 沒有任何改動。

## 字幕與疊字的字級

- **為什麼**：實際在手機上看，黃色標題與白色字幕都偏小。
- **改了什麼**：`subtitles.size`（`normal`／`large`／`xlarge`）；疊字多一個 `xlarge`，最多折成三行（原本兩行，長標題會被截掉）。
- **相容性**：沒寫 `size` 的 manifest 字級不變。疊字 `large` 以下的大小不變。

## 文案直出（`vidpipe quick`、`POST /api/quick`）與素材不重複

- **為什麼**：原本從文案到成片要先有人（或 LLM）替每個場景想搜尋關鍵字。使用者希望貼上文案就能做，單筆或整批；
  也發現連續做幾支片時畫面素材重複率太高。
- **改了什麼**：新增 `vidpipe.quick`：規則式分鏡、用關鍵詞對應 30 個畫面主題、每個主題一個 vidgrab 素材池、
  加大的標題與字幕、背景音樂。別的作品用過的素材不再用，不夠就換關鍵字再搜。CLI `vidpipe quick`（單筆、CSV、JSON、JSONL）、
  HTTP `POST /api/quick` 與 `/api/quick/preview`。`studio/` 改成共用這個模組，不再有自己的一份。
- **相容性**：既有指令與端點不變；`data/footage.json` 隨套件安裝。

## `POST /api/restart`

- **為什麼**：本機用 `nohup` 起的 `vidpipe serve` 在 `git pull` 之後要重啟才會載入新版，原本得到終端機找行程。
- **改了什麼**：`POST /api/restart` 回 202 之後停掉伺服器，用同樣的參數（加上 `--no-open`）重新執行自己。
  跟其他寫入類端點一樣受 token、Host 與 Origin 檢查保護。

## 抓不下來的素材自動換掉

- **為什麼**：重做 6 支 Shorts 時，有 3 支因為同一支 YouTube 素材已經下架（`This video is not available`）而整支失敗。
- **改了什麼**：`vidpipe quick` 與 `studio` 遇到「vidgrab 抓不到 <素材>」時，把那支素材記進 `work_dir/_quick/bad.json`、
  換一支重挑再做（最多 4 次）；之後所有作品都不會再挑到它。`studio batch` 把日期寫進批次計畫，隔天續跑仍接著同一組 run。
## 參考音檔資料夾與自動逐字稿（`voice_ref_dir`、`vidpipe voices`）

- **為什麼**：換音色時要自己找出參考音檔說了什麼、貼進 `reference_text`；音檔一多就很麻煩，也容易打錯。
- **改了什麼**：新設定 `voice_ref_dir`、`asr_model`、`asr_language`。資料夾裡沒有同名 `.txt` 的音檔，用 audio.cpp 的
  ASR 辨識、轉成台灣繁體後存回同一個資料夾。`voice_ref` 可以只寫檔名；`reference_text` 留空時自動讀同名 `.txt`。
  CLI `vidpipe voices [--transcribe]`、`GET /api/voices`、`POST /api/voices/transcribe`、設定頁的「掃描參考音檔」；
  存了新的資料夾會在背景補逐字稿。新增相依 `opencc-python-reimplemented`（純 Python）。
- **相容性**：沒設 `voice_ref_dir` 時行為不變；有填 `reference_text` 就用填的。

## 素材畫面燒進文字（浮水印、宣傳字卡）自動換掉

- **為什麼**：6 支 Shorts 裡有 5 支用到畫面上壓著文字的 YouTube 素材：「JOIN OUR MEMBERSHIP」、「PURCHASE HD CLIPS」、
  「Download 4K…」，還有 pond5 的「Exclusive on pond5.com」（那是未授權的預覽版浮水印）。
- **改了什麼**：`vidpipe.ocr` 用 tesseract 掃描成片每個場景的中間一帶（避開標題與字幕），有清楚可辨的文字就把那支素材
  記進 `work_dir/_quick/bad.json`、換一支重做（跟「抓不下來」同一套重挑，最多 4 次）。沒有安裝 tesseract（例如 Docker 映像）
  時整個檢查略過，不影響出片。`studio` 共用同一個偵測。
- **限制**：只認英數字；路牌之類的真實文字也會被當成可疑而換掉（寧可多換）。中文浮水印不在偵測範圍。
