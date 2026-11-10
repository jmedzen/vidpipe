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
