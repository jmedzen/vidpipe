# Changelog

記錄「為什麼需要這個改動」，以及 merge 與上線驗證的結果。

## 本機檔案可以宣告授權（`clip.license`／`music.license`）

- **為什麼**：下游的 vidoutput 流水線在沒有素材站 API key 時用自己生成的背景畫面（`clip.path`）。
  這些檔案在 `credits.md` 一律被標成「本機檔案，授權請自行確認」並列進 `unclear`，
  自動品質檢查無法分辨「自製素材」與「真的沒確認過授權的檔案」。
- **改了什麼**：manifest 的 `clip` 與 `music` 用 `path` 時可加 `license`（`own`／`free`／`free-attribution`）
  與 `credit`。宣告寫進 `sources.json`（`declared_license`／`declared_credit`），`credits.md` 與摘要照宣告分類。
- **相容性**：新增的選填欄位；沒寫的 manifest 行為完全不變（schema 仍是 `vidpipe.manifest/1`）。
