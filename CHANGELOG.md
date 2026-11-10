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
